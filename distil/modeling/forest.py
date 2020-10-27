SUPRESS_WARNINGS = True
if SUPRESS_WARNINGS:
    import sys
    def warn(*args, **kwargs): pass

    import warnings
    warnings.warn = warn

import sys
import numpy as np
import pandas as pd
from copy import deepcopy

from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor, \
    ExtraTreesRegressor, ExtraTreesClassifier

from sklearn.model_selection import ParameterGrid

from .base import DistilBaseModel
from .metrics import metrics, classification_metrics, regression_metrics
from .helpers import tiebreaking_vote_pre, adjust_f1_macro
from ..utils import parmap, maybe_subset

class AnyForest:
    __possible_model_cls = {
        ("regression",     "ExtraTrees")   : ExtraTreesRegressor,
        ("regression",     "RandomForest") : RandomForestRegressor,
        ("classification", "ExtraTrees")   : ExtraTreesClassifier,
        ("classification", "RandomForest") : RandomForestClassifier,
    }

    def __init__(self, mode, estimator, **kwargs):
        assert (mode, estimator) in self.__possible_model_cls
        self.mode   = mode

        self.params    = kwargs
        self.model_cls = self.__possible_model_cls[(mode, estimator)]

    def fit(self, X, y):
        # if self.mode == 'classification':
        #     assert y.dtype == int
        #     assert y.min() == 0, 'may need to remap_labels'
        #     assert y.max() == len(set(y)) - 1, 'may need to remap_labels'
        y = y.ravel(order='C')
        self.model = self.model_cls(**self.params).fit(X, y)
        return self

    def predict(self, X):
        return self.model.predict(X)

    def predict_proba(self, X):
        return self.model.predict_proba(X)

    def predict_oob(self):
        if self.mode == 'regression':
            return self.model.oob_prediction_
        elif self.mode == 'classification':
            score_oob = self.model.oob_decision_function_
            return self.model.classes_[score_oob.argmax(axis=-1)] # could vote better

    def feature_importances(self):
        return self.model.feature_importances_


class ForestCV(DistilBaseModel):
    default_param_grids = {
        "classification" : {
            "estimator"        : ["RandomForest"],
            "n_estimators"     : [32, 64, 128, 256, 512, 1024, 2048],
            "min_samples_leaf" : [1, 2, 4, 8, 16, 32],
            "class_weight"     : [None, "balanced"],
        },
        "regression" : {
            "estimator"        : ["ExtraTrees", "RandomForest"],
            "bootstrap"        : [True],
            "n_estimators"     : [32, 64, 128, 256, 512, 1024, 2048],
            "min_samples_leaf" : [2, 4, 8, 16, 32, 64],
        }
    }

    def __init__(self, target_metric, subset=100000, final_subset=1500000,
        verbose=10, num_fits=1, inner_jobs=1, grid_search=False, param_grid=None, random_seed=None,
        hyperparams=None, n_jobs=64):

        self.target_metric = target_metric

        if target_metric in classification_metrics:
            self.mode = 'classification'
        elif target_metric in regression_metrics:
            self.mode = 'regression'
        else:
            raise Exception(f'ForestCV: unknown metric {target_metric}')

        self.subset       = subset
        self.final_subset = final_subset
        self.verbose      = verbose
        self.num_fits     = num_fits
        self.inner_jobs   = inner_jobs
        self.outer_jobs   = n_jobs

        self.params = hyperparams
        self.random_seed = random_seed

        # optional support for internal grid search
        if grid_search:
            if param_grid is not None:
                self.param_grid = param_grid
            else:
                self.param_grid = deepcopy(self.default_param_grids[self.mode])
        else:
            self.param_grid = None

        self._models  = []
        self._y_train = None

    def fit(self, Xf_train, y_train, U_train=None):
        self._y_train = y_train
        self._models  = [self._fit(Xf_train, y_train, self.param_grid) for _ in range(self.num_fits)]
        return self

    # def score(self, X, y):
    #     # !! May want to adjust F1 score.  ... but need to figure out when and whether it's helping
    #     return metrics[self.target_metric](y, self.predict(X))

    def predict(self, X):
        preds = [model.predict(X) for model in self._models]

        if self.mode == 'classification':
            labels = pd.unique(np.vstack(self._y_train).squeeze())
            return tiebreaking_vote_pre(np.vstack(preds), labels)
        elif self.mode == 'regression':
            return np.stack(preds).mean(axis=0)

    def predict_proba(self, X):
        preds = self._models[0].predict_proba(X)
        return preds


    def feature_importances(self):
        return self._models[0].feature_importances()

    def _eval_grid_point(self, params, X, y, random_seed=None):
        params['random_state'] = random_seed
        model = AnyForest(
            mode=self.mode,
            oob_score=True,
            n_jobs=self.inner_jobs,
            **params,
        )

        model       = model.fit(X, y)
        oob_fitness = metrics[self.target_metric](y, model.predict_oob())
        return {"params" : params, "fitness" : oob_fitness}

    def _fit(self, Xf_train, y_train, param_grid=None):

        X, y = maybe_subset(Xf_train, y_train, n=self.subset)

        # Run grid search
        if param_grid is not None:
            self.results = parmap(self._eval_grid_point,
                ParameterGrid(self.param_grid), X=X, y=y, verbose=self.verbose, n_jobs=self.outer_jobs, random_seed=self.random_seed)

            # Find best run
            best_run = sorted(self.results, key=lambda x: x['fitness'])[-1] # bigger is better
            self.best_params, self.best_fitness = best_run['params'], best_run['fitness']

            # Refit best model, possibly on more data
            X, y  = maybe_subset(Xf_train, y_train, n=self.final_subset)
            model = AnyForest(mode=self.mode, n_jobs=self.outer_jobs, **self.best_params)
            model = model.fit(X, y)
        else:
            current_params = self.params
            current_params['random_state'] = self.random_seed
            model = AnyForest(mode=self.mode, n_jobs=self.outer_jobs, **current_params)
            model = model.fit(X, y)

        return model

    @property
    def details(self):
        return {
            "cv_score"    : self.best_fitness,
            "best_params" : self.best_params,
            "num_fits"    : self.num_fits,
        }
