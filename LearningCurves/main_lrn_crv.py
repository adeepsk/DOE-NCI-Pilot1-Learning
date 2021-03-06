""" 
This script generates multiple learning curves for different training sets.
It launches a script (e.g. trn_lrn_crv.py) that train ML model(s) on various training set sizes.
"""
from __future__ import print_function, division

import warnings
warnings.filterwarnings('ignore')

import os
import sys
from pathlib import Path
import argparse
from datetime import datetime
from time import time
from pprint import pprint, pformat
from collections import OrderedDict
from glob import glob

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import sklearn
import numpy as np
import pandas as pd

from sklearn.preprocessing import StandardScaler, MinMaxScaler, RobustScaler

SEED = 42


# File path
# file_path = os.path.dirname(os.path.realpath(__file__))
file_path = Path(__file__).resolve().parent


# Utils
utils_path = file_path / '../../utils'
sys.path.append(str(utils_path))
import utils
# from utils_tidy import load_tidy_combined, get_data_by_src, break_src_data 
from classlogger import Logger
from lrn_crv import LearningCurve


# Path
PRJ_NAME = file_path.name 
OUTDIR = file_path / '../../out/' / PRJ_NAME
    
        
def parse_args(args):
    parser = argparse.ArgumentParser(description="Generate learning curves.")

    # Input data
    parser.add_argument('--dirpath', default=None, type=str, help='Full path to data and splits (default: None).')

    # Select data name
    # parser.add_argument('--dname', default=None, choices=['combined'], help='Data name (default: `combined`).')

    # Select (cell line) sources 
    # parser.add_argument('-src', '--src_names', nargs='+',
    #     default=None, choices=['ccle', 'gcsi', 'gdsc', 'ctrp', 'nci60'],
    #     help='Data sources to use (relevant only for the `combined` dataset).')

    # Select target to predict
    parser.add_argument('-t', '--target_name', default='AUC', type=str, choices=['AUC', 'AUC1', 'IC50'], help='Name of target variable (default: AUC).')

    # Select feature types
    parser.add_argument('-cf', '--cell_features', nargs='+', default=['rna'], choices=['rna', 'cnv', 'clb'], help='Cell line features (default: rna).')
    parser.add_argument('-df', '--drug_features', nargs='+', default=['dsc'], choices=['dsc', 'fng', 'dlb'], help='Drug features (default: dsc).')
    parser.add_argument('-of', '--other_features', default=[], choices=[],
            help='Other feature types (derived from cell lines and drugs). E.g.: cancer type, etc).') # ['cell_labels', 'drug_labels', 'ctype', 'csite', 'rna_clusters']

    # Data split methods
    parser.add_argument('-cvm', '--cv_method', default='simple', type=str, choices=['simple', 'group'], help='CV split method (default: simple).')
    parser.add_argument('-cvf', '--cv_folds', default=5, type=str, help='Number cross-val folds (default: 5).')
    
    # ML models
    # parser.add_argument('-frm', '--framework', default='lightgbm', type=str, choices=['keras', 'lightgbm', 'sklearn'], help='ML framework (default: lightgbm).')
    parser.add_argument('-ml', '--model_name', default='lgb_reg', type=str, 
            # choices=['lgb_reg', 'rf_reg', 'nn_reg', 'nn_reg0', 'nn_reg1', 'nn_reg2', 'nn_reg3', 'nn_reg4'],
            help='ML model for training (default: lgb_reg).')

    # NN hyper_params
    parser.add_argument('-ep', '--epochs', default=200, type=int, help='Number of epochs (default: 200).')
    parser.add_argument('--batch_size', default=32, type=int, help='Batch size (default: 32).')
    parser.add_argument('--dr_rate', default=0.2, type=float, help='Dropout rate (default: 0.2).')
    parser.add_argument('-sc', '--scaler', default='stnd', type=str, choices=['stnd', 'minmax', 'rbst'], help='Feature normalization method (default: stnd).')

    parser.add_argument('--opt', default='sgd', type=str, choices=['sgd', 'adam'], help='Optimizer name (default: sgd).')

    parser.add_argument('--clr_mode', default=None, type=str, choices=['trng1', 'trng2', 'exp'], help='CLR mode (default: trng1).')
    parser.add_argument('--clr_base_lr', type=float, default=1e-4, help='Base lr for cycle lr.')
    parser.add_argument('--clr_max_lr', type=float, default=1e-3, help='Max lr for cycle lr.')
    parser.add_argument('--clr_gamma', type=float, default=0.999994, help='Gamma parameter for learning cycle LR.')

    # Learning curve
    parser.add_argument('--n_shards', default=5, type=int, help='Number of ticks in the learning curve plot (default: 5).')

    # Define n_jobs
    parser.add_argument('--n_jobs', default=4, type=int, help='Default: 4.')

    # Parse args
    args = parser.parse_args(args)
    return args
        
    
def create_outdir(outdir, args, src):
    t = datetime.now()
    t = [t.year, '-', t.month, '-', t.day, '_', 'h', t.hour, '-', 'm', t.minute]
    t = ''.join([str(i) for i in t])
    
    l = [('cvf'+str(args['cv_folds']))] + args['cell_features'] + args['drug_features'] + [args['target_name']] 
    if args['clr_mode'] is not None: l = [args['clr_mode']] + l
    if 'nn' in args['model_name']: l = [args['opt']] + l
                
    name_sffx = '.'.join( [src] + [args['model_name']] + l )
    outdir = Path(outdir) / (name_sffx + '_' + t)
    #outdir = Path(outdir) / name_sffx
    os.makedirs(outdir)
    #os.makedirs(outdir, exist_ok=True)
    return outdir    
    
    
def run(args):
    dirpath = Path(args['dirpath'])
    # dname = args['dname']
    # src_names = args['src_names']
   
    # Target
    target_name = args['target_name']

    # Data split 
    cv_folds = args['cv_folds']

    # Features 
    cell_fea = args['cell_features']
    drug_fea = args['drug_features']
    other_fea = args['other_features']
    fea_list = cell_fea + drug_fea + other_fea    

    # NN params
    epochs = args['epochs']
    batch_size = args['batch_size']
    dr_rate = args['dr_rate']

    # Optimizer
    opt_name = args['opt']
    clr_keras_kwargs = {'mode': args['clr_mode'], 'base_lr': args['clr_base_lr'],
                        'max_lr': args['clr_max_lr'], 'gamma': args['clr_gamma']}

    # Learning curve
    n_shards = args['n_shards']

    # Other params
    # framework = args['framework']
    model_name = args['model_name']
    n_jobs = args['n_jobs']

    # ML type ('reg' or 'cls')
    if 'reg' in model_name:
        mltype = 'reg'
    elif 'cls' in model_name:
        mltype = 'cls'
    else:
        raise ValueError("model_name must contain 'reg' or 'cls'.")

    # Define metrics
    # metrics = {'r2': 'r2',
    #            'neg_mean_absolute_error': 'neg_mean_absolute_error', #sklearn.metrics.neg_mean_absolute_error,
    #            'neg_median_absolute_error': 'neg_median_absolute_error', #sklearn.metrics.neg_median_absolute_error,
    #            'neg_mean_squared_error': 'neg_mean_squared_error', #sklearn.metrics.neg_mean_squared_error,
    #            'reg_auroc_score': utils.reg_auroc_score}
    
    
    # ========================================================================
    #       Load data and pre-proc
    # ========================================================================
    dfs = {}

    def get_file(fpath):
        return pd.read_csv(fpath, header=None).squeeze().values if fpath.is_file() else None

    def read_data_file(fpath, file_format='csv'):
        fpath = Path(fpath)
        if fpath.is_file():
            if file_format=='csv':
                df = pd.read_csv( fpath )
            elif file_format=='parquet':
                df = pd.read_parquet( fpath )
        else:
            df = None
        return df
    
    if dirpath is not None:
        xdata = read_data_file( dirpath/'xdata.parquet', 'parquet' )
        meta  = read_data_file( dirpath/'meta.parquet', 'parquet' )
        ydata = meta[[target_name]]
    
        tr_id = pd.read_csv( dirpath/f'{cv_folds}fold_tr_id.csv' )
        vl_id = pd.read_csv( dirpath/f'{cv_folds}fold_vl_id.csv' )

        # tr_ids_list = get_file( dirpath/f'{cv_folds}fold_tr_id.csv' )
        # vl_ids_list = get_file( dirpath/f'{cv_folds}fold_vl_id.csv' )
        # te_ids_list = get_file( dirpath/f'{cv_folds}fold_te_id.csv' )

        src = dirpath.name.split('_')[0]
        dfs[src] = (ydata, xdata, tr_id, vl_id) 
            
    elif dname == 'combined':
        # TODO: this is not used anymore (probably won't work)
        DATADIR = file_path / '../../data/processed/data_splits'
        DATAFILENAME = 'data.parquet'
        dirs = glob( str(DATADIR/'*') )
        
        for src in src_names:
            print(f'\n{src} ...')
            subdir = f'{src}_cv_{cv_method}'
            if str(DATADIR/subdir) in dirs:
                # Get the CV indexes
                tr_id = pd.read_csv( DATADIR/subdir/f'{cv_folds}fold_tr_id.csv' )
                vl_id = pd.read_csv( DATADIR/subdir/f'{cv_folds}fold_vl_id.csv' )

                # Get the data
                datapath = DATADIR / subdir / DATAFILENAME
                data = pd.read_parquet( datapath )
                xdata, _, meta, _ = break_src_data(data, target=None, scaler=None) # logger=lg.logger
                ydata = meta[[target_name]]
                
                dfs[src] = (ydata, xdata, tr_id, vl_id)
                del data, xdata, ydata, tr_id, vl_id, src

    
    for src, data in dfs.items():
        ydata, xdata, tr_id, vl_id = data[0], data[1], data[2], data[3]

        # Scale 
        scaler = args['scaler']
        if scaler is not None:
            if scaler == 'stnd':
                scaler = StandardScaler()
            elif scaler == 'minmax':
                scaler = MinMaxScaler()
            elif scaler == 'rbst':
                scaler = RobustScaler()
        
        cols = xdata.columns
        xdata = pd.DataFrame(scaler.fit_transform(xdata), columns=cols, dtype=np.float32)


        # -----------------------------------------------
        #       Create outdir and logger
        # -----------------------------------------------
        run_outdir = create_outdir(OUTDIR, args, src)
        lg = Logger(run_outdir/'logfile.log')
        lg.logger.info(f'File path: {file_path}')
        lg.logger.info(f'\n{pformat(args)}')

        # Dump args to file
        utils.dump_dict(args, outpath=run_outdir/'args.txt')        


        # -----------------------------------------------
        #      ML model configs
        # -----------------------------------------------
        if model_name == 'lgb_reg':
            framework = 'lightgbm'
            init_kwargs = {'n_jobs': n_jobs, 'random_state': SEED, 'logger': lg.logger}
            fit_kwargs = {'verbose': False}
        elif model_name == 'nn_reg':
            framework = 'keras'
            init_kwargs = {'input_dim': xdata.shape[1], 'dr_rate': dr_rate, 'opt_name': opt_name, 'attn': attn, 'logger': lg.logger}
            fit_kwargs = {'batch_size': batch_size, 'epochs': epochs, 'verbose': 1} 
        elif model_name == 'nn_reg0' or 'nn_reg1' or 'nn_reg2':
            framework = 'keras'
            init_kwargs = {'input_dim': xdata.shape[1], 'dr_rate': dr_rate, 'opt_name': opt_name, 'logger': lg.logger}
            fit_kwargs = {'batch_size': batch_size, 'epochs': epochs, 'verbose': 1}  # 'validation_split': 0.1
        elif model_name == 'nn_reg3' or 'nn_reg4':
            framework = 'keras'
            init_kwargs = {'in_dim_rna': None, 'in_dim_dsc': None, 'dr_rate': dr_rate, 'opt_name': opt_name, 'logger': lg.logger}
            fit_kwargs = {'batch_size': batch_size, 'epochs': epochs, 'verbose': 1}  # 'validation_split': 0.1


        # -----------------------------------------------
        #      Learning curve 
        # -----------------------------------------------
        lg.logger.info('\n\n{}'.format('=' * 50))
        lg.logger.info(f'Learning curves {src} ...')
        lg.logger.info('=' * 50)

        t0 = time()
        lc = LearningCurve( X=xdata, Y=ydata, cv=None, cv_lists=(tr_id, vl_id),
            n_shards=n_shards, shard_step_scale='log10', args=args,
            logger=lg.logger, outdir=run_outdir )

        lrn_crv_scores = lc.trn_learning_curve( framework=framework, mltype=mltype, model_name=model_name,
            init_kwargs=init_kwargs, fit_kwargs=fit_kwargs, clr_keras_kwargs=clr_keras_kwargs,
            n_jobs=n_jobs, random_state=SEED )

        lg.logger.info('Runtime: {:.1f} hrs'.format( (time()-t0)/360) )


        # -------------------------------------------------
        # Learning curve (sklearn method)
        # Problem! cannot log multiple metrics.
        # -------------------------------------------------
        """
        lg.logger.info('\nStart learning curve (sklearn method) ...')
        # Define params
        metric_name = 'neg_mean_absolute_error'
        base = 10
        train_sizes_frac = np.logspace(0.0, 1.0, lc_ticks, endpoint=True, base=base)/base

        # Run learning curve
        t0 = time()
        lrn_curve_scores = learning_curve(
            estimator=model.model, X=xdata, y=ydata,
            train_sizes=train_sizes_frac, cv=cv, groups=groups,
            scoring=metric_name,
            n_jobs=n_jobs, exploit_incremental_learning=False,
            random_state=SEED, verbose=1, shuffle=False)
        lg.logger.info('Runtime: {:.1f} mins'.format( (time()-t0)/60) )

        # Dump results
        # lrn_curve_scores = utils.cv_scores_to_df(lrn_curve_scores, decimals=3, calc_stats=False) # this func won't work
        # lrn_curve_scores.to_csv(os.path.join(run_outdir, 'lrn_curve_scores_auto.csv'), index=False)

        # Plot learning curves
        lrn_crv.plt_learning_curve(rslt=lrn_curve_scores, metric_name=metric_name,
            title='Learning curve (target: {}, data: {})'.format(target_name, tr_sources_name),
            path=os.path.join(run_outdir, 'auto_learning_curve_' + target_name + '_' + metric_name + '.png'))
        """
        
        lg.kill_logger()
        del xdata, ydata
        
    print('Done.')


def main(args):
    args = parse_args(args)
    args = vars(args)
    run(args)
    

if __name__ == '__main__':
    main(sys.argv[1:])


