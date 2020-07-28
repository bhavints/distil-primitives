import os
import typing
from math import log

import numpy as np
from scipy.sparse import issparse
from scipy.special import digamma, gamma
import pandas as pd  # type: ignore
from d3m import container, utils as d3m_utils
from d3m import exceptions
from d3m.metadata import base as metadata_base, hyperparams
from d3m.primitive_interfaces import base, transformer
from distil.utils import CYTHON_DEP
from sklearn.feature_selection import mutual_info_regression, mutual_info_classif
from sklearn import metrics
from sklearn import preprocessing
from sklearn import utils as skl_utils
from sklearn.neighbors import NearestNeighbors
from sklearn.feature_extraction.text import TfidfVectorizer
import version

__all__ = ('MIRankingPrimitive',)


class Hyperparams(hyperparams.Hyperparams):
    target_col_index = hyperparams.Hyperparameter[typing.Optional[int]](
        default=None,
        semantic_types=['https://metadata.datadrivendiscovery.org/types/ControlParameter'],
        description='Index of target feature to rank against.'
    )
    k = hyperparams.Hyperparameter[typing.Optional[int]](
        default=3,
        semantic_types=['https://metadata.datadrivendiscovery.org/types/ControlParameter'],
        description='Number of clusters for k-nearest neighbors'
    )

class MIRankingPrimitive(transformer.TransformerPrimitiveBase[container.DataFrame,
                                                              container.DataFrame,
                                                              Hyperparams]):
    """
    Feature ranking based on a mutual information between features and a selected
    target.  Will rank any feature column with a semantic type of Float, Boolean,
    Integer or Categorical, and a corresponding structural type of int, float or str.
    Features that could not be ranked are excluded from the returned set.
    Parameters
    ----------
    inputs : A container.Dataframe with columns containing numeric or string data.
    Returns
    -------
    output : A DataFrame containing (col_idx, col_name, score) tuples for each ranked feature.
    """

    # allowable target column types
    _discrete_types = (
        'http://schema.org/Boolean',
        'http://schema.org/Integer',
        'https://metadata.datadrivendiscovery.org/types/CategoricalData'
    )

    _continous_types = (
        'http://schema.org/Float',
    )

    _text_semantic = (
        'http://schema.org/Text',
    )

    _roles = (
        'https://metadata.datadrivendiscovery.org/types/Attribute',
        'https://metadata.datadrivendiscovery.org/types/Target',
        'https://metadata.datadrivendiscovery.org/types/TrueTarget',
        'https://metadata.datadrivendiscovery.org/types/SuggestedTarget',
    )

    _structural_types = set((
        int,
        float
    ))

    _semantic_types = set(_discrete_types).union(_continous_types)

    _random_seed = 100

    __author__ = 'Uncharted Software',
    metadata = metadata_base.PrimitiveMetadata(
        {
            'id': 'a31b0c26-cca8-4d54-95b9-886e23df8886',
            'version': version.__version__,
            'name': 'Mutual Information Feature Ranking',
            'python_path': 'd3m.primitives.feature_selection.mutual_info_classif.DistilMIRanking',
            'keywords': ['vector', 'columns', 'dataframe'],
            'source': {
                'name': 'Distil',
                'contact': 'mailto:cbethune@uncharted.software',
                'uris': [
                    'https://github.com/uncharted-distil/distil-primitives/distil/primitives/mi_ranking.py',
                    'https://github.com/uncharted-distil/distil-primitives/',
                ]
            },
            'installation': [CYTHON_DEP, {
                'type': metadata_base.PrimitiveInstallationType.PIP,
                'package_uri': 'git+https://github.com/uncharted-distil/distil-primitives.git@{git_commit}#egg=distil-primitives'.format(
                    git_commit=d3m_utils.current_git_commit(os.path.dirname(__file__)),
                ),
            }],
            'algorithm_types': [
                metadata_base.PrimitiveAlgorithmType.MUTUAL_INFORMATION,
            ],
            'primitive_family': metadata_base.PrimitiveFamily.DATA_PREPROCESSING,
        }
    )

    @classmethod
    def _can_use_column(cls, inputs_metadata: metadata_base.DataMetadata, column_index: typing.Optional[int]) -> bool:

        column_metadata = inputs_metadata.query((metadata_base.ALL_ELEMENTS, column_index))

        valid_struct_type = column_metadata.get('structural_type', None) in cls._structural_types
        semantic_types = column_metadata.get('semantic_types', [])
        valid_semantic_type = len(set(cls._semantic_types).intersection(semantic_types)) > 0
        valid_role_type = len(set(cls._roles).intersection(semantic_types)) > 0

        return valid_struct_type and valid_semantic_type

    @classmethod
    def _append_rank_info(cls,
                          inputs: container.DataFrame,
                          result: typing.List[typing.Tuple[int, str, float]],
                          rank_np: np.array,
                          rank_df: pd.DataFrame) -> typing.List[typing.Tuple[int, str, float]]:
        for i, rank in enumerate(rank_np):
            col_name = rank_df.columns.values[i]
            result.append((inputs.columns.get_loc(col_name), col_name, rank))
        return result

    def produce(self, *,
                inputs: container.DataFrame,
                timeout: float = None,
                iterations: int = None) -> base.CallResult[container.DataFrame]:

        cols = ['idx', 'name', 'rank']

        # Make sure the target column is of a valid type and return no ranked features if it isn't.
        target_idx = self.hyperparams['target_col_index']
        if not self._can_use_column(inputs.metadata, target_idx):
            return base.CallResult(container.DataFrame(data={}, columns=cols))

        # check if target is discrete or continuous
        semantic_types = inputs.metadata.query_column(target_idx)['semantic_types']
        discrete = len(set(semantic_types).intersection(self._discrete_types)) > 0

        # make a copy of the inputs and clean out any missing data
        feature_df = inputs.copy()
        feature_df.dropna(inplace=True)

        # split out the target feature
        target_df = feature_df.iloc[:, target_idx]

        # drop features that are not compatible with ranking
        feature_indices = set(inputs.metadata.list_columns_with_semantic_types(self._semantic_types))
        role_indices = set(inputs.metadata.list_columns_with_semantic_types(self._roles))
        feature_indices = feature_indices.intersection(role_indices)
        feature_indices.remove(target_idx)
        for categ_ind in inputs.metadata.list_columns_with_semantic_types(('https://metadata.datadrivendiscovery.org/types/CategoricalData',)):
            if np.unique(inputs[inputs.columns[categ_ind]]).shape[0] == inputs.shape[0] and categ_ind in feature_indices:
                feature_indices.remove(categ_ind)
        text_indices = inputs.metadata.list_columns_with_semantic_types(self._text_semantic)

        tfv = TfidfVectorizer()
        column_to_text_features = {}
        for text_index in text_indices:
            if text_index not in feature_indices and text_index in role_indices and text_index != target_idx:
                word_features = tfv.fit_transform(inputs[inputs.columns[text_index]])
                column_to_text_features[inputs.columns[text_index]] = word_features

        # return an empty result if all features were incompatible
        numeric_features = len(feature_indices) > 0
        if not numeric_features and len(column_to_text_features) == 0:
            return base.CallResult(container.DataFrame(data={}, columns=cols))

        all_indices = set(range(0, inputs.shape[1]))
        skipped_indices = all_indices.difference(feature_indices)
        for i, v in enumerate(skipped_indices):
            feature_df.drop(inputs.columns[v], axis=1, inplace=True)

        # figure out the discrete and continuous feature indices and create an array
        # that flags them
        discrete_indices = inputs.metadata.list_columns_with_semantic_types(self._discrete_types)
        discrete_flags = [False] * feature_df.shape[1]
        for v in discrete_indices:
            col_name = inputs.columns[v]
            if col_name in feature_df:
                # only mark columns with a least 1 duplicate value as discrete when predicting
                # a continuous target - there's a check in the bowels of MI code that will throw
                # an exception otherwise
                if feature_df[col_name].duplicated().any() and not discrete:
                    col_idx = feature_df.columns.get_loc(col_name)
                    discrete_flags[col_idx] = True

        target_np = target_df.values
        feature_np = feature_df.values

        # compute mutual information for discrete or continuous target
        ranked_features_np = None
        if discrete:
            if numeric_features:
                ranked_features_np = mutual_info_classif(feature_np,
                                                        target_np,
                                                        discrete_features=discrete_flags,
                                                        n_neighbors=self.hyperparams['k'],
                                                        random_state=self._random_seed)
            for column in column_to_text_features:
                text_rankings = mutual_info_classif(column_to_text_features[column],
                                                                    target_np,
                                                                    discrete_features=[True] * column_to_text_features[column].shape[0],
                                                                    n_neighbors=self.hyperparams['k'],
                                                                    random_state=self._random_seed)
                max_text_rank_index = np.argmax(text_rankings)
                ranked_features_np = np.append(ranked_features_np, text_rankings[max_text_rank_index])
                max_rank_text_feature = column_to_text_features[column][:, max_text_rank_index]
                if issparse(max_rank_text_feature):
                    feature_df[column] = pd.DataFrame.sparse.from_spmatrix(max_rank_text_feature)
                else:
                    feature_df[column] = max_rank_text_feature
                discrete_flags.append(True)
        else:
            if numeric_features:
                ranked_features_np = mutual_info_regression(feature_np,
                                                            target_np,
                                                            discrete_features=discrete_flags,
                                                            n_neighbors=self.hyperparams['k'],
                                                            random_state=self._random_seed)
            for column in column_to_text_features:
                text_rankings = mutual_info_regression(column_to_text_features[column],
                                                                    target_np,
                                                                    discrete_features=[True] * len(column_to_text_features[column]),
                                                                    n_neighbors=self.hyperparams['k'],
                                                                    random_state=self._random_seed)
                max_text_rank_index = np.argmax(text_rankings)
                ranked_features_np = np.append(ranked_features_np, text_rankings[max_text_rank_index])
                max_rank_text_feature = column_to_text_features[column][:, max_text_rank_index]
                if issparse(max_rank_text_feature):
                    feature_df[column] = pd.DataFrame.sparse.from_spmatrix(max_rank_text_feature)
                else:
                    feature_df[column] = max_rank_text_feature
                discrete_flags.append(True)

        ranked_features_np = self._normalize(ranked_features_np, feature_df, target_np, discrete, discrete_flags)

        # merge back into a single list of col idx / rank value tuples
        data: typing.List[typing.Tuple[int, str, float]] = []
        data = self._append_rank_info(inputs, data, ranked_features_np, feature_df)
        # for column in ranked_text_features:
        #     data = self._append_rank_info(inputs, data, ranked_text_features[column], column_to_text_features[])

        # wrap as a D3M container - metadata should be auto generated
        results = container.DataFrame(data=data, columns=cols, generate_metadata=True)
        results = results.sort_values(by=['rank'], ascending=False).reset_index(drop=True)

        return base.CallResult(results)

    def _normalize(self, ranked_features, feature_df, target_np, discrete, discrete_flags):
        normalized_ranked_features = np.empty(ranked_features.shape[0])
        if discrete:
            target_entropy = self._discrete_entropy(target_np)
            for i in range(ranked_features.shape[0]):
                if discrete_flags[i]:
                    normalized_ranked_features[i] = metrics.normalized_mutual_info_score(target_np, feature_df.iloc[:, i], average_method='geometric')
                else:
                    feature_entropy = self._continuous_entropy(feature_df.iloc[:, i])
                    normalized_ranked_features[i] = ranked_features[i] / np.sqrt(feature_entropy * target_entropy)
                if normalized_ranked_features[i] > 1.0:
                    normalized_ranked_features[i] = 1.0
            # target_entropy = self._discrete_entropy(target_np)
            # for i in range(ranked_features.shape[0]):
            #     if discrete_flags[i]:
            #         normalized_ranked_features[i] = ranked_features[i] / np.sqrt(self._discrete_entropy(feature_np[:, i]) * target_entropy)
            #         if normalized_ranked_features[i] > 1:
            #             normalized_ranked_features[i] = 1.0
            #     else:
            #         ksg_entropy, naive_entropy = self._continuous_entropy(feature_np[:, i])
            #         ksg_mi = ranked_features[i] / np.sqrt(ksg_entropy * target_entropy)
            #         naive_mi = ranked_features[i] / np.sqrt(naive_entropy * target_entropy)
            #         if abs(ksg_mi - 1) < abs(naive_mi - 1):
            #             if ksg_mi > 1:
            #                 normalized_ranked_features[i] = 1.0
            #             else:
            #                 normalized_ranked_features[i] = ksg_mi
            #         else:
            #             if naive_mi > 1:
            #                 normalized_ranked_features[i] = 1.0
            #             else:
            #                 normalized_ranked_features[i] = naive_mi
        else:
            target_entropy = self._continuous_entropy(target_np)
            for i in range(ranked_features.shape[0]):
                if discrete_flags[i]:
                    feature_entropy = self._discrete_entropy(feature_df.iloc[:, i])
                    normalized_ranked_features[i] = ranked_features[i] / np.sqrt(feature_entropy * target_entropy)
                else:
                    feature_entropy = self._continuous_entropy(feature_df.iloc[:, i])
                    normalized_ranked_features[i] = ranked_features[i] / np.sqrt(feature_entropy * target_entropy)
                if normalized_ranked_features[i] > 1.0:
                    normalized_ranked_features[i] = 1.0
            # target_ksg_entropy, target_naive_entropy = self._continuous_entropy(target_np)
            # for i in range(ranked_features.shape[0]):
            #     if discrete_flags[i]:
            #         feature_entropy = self._discrete_entropy(feature_np[:, i])
            #         ksg_mi = ranked_features[i] / np.sqrt(feature_entropy * target_ksg_entropy)
            #         naive_mi = ranked_features[i] / np.sqrt(feature_entropy * target_naive_entropy)
            #     else:
            #         ksg_entropy, naive_entropy = self._continuous_entropy(feature_np[:, i])
            #         ksg_mi = ranked_features[i] / np.sqrt(ksg_entropy * target_ksg_entropy)
            #         naive_mi = ranked_features[i] / np.sqrt(naive_entropy * target_naive_entropy)
            #     if abs(ksg_mi - 1) < abs(naive_mi - 1):
            #         if ksg_mi > 1:
            #             normalized_ranked_features[i] = 1.0
            #         else:
            #             normalized_ranked_features[i] = ksg_mi
            #     else:
            #         if naive_mi > 1:
            #             normalized_ranked_features[i] = 1.0
            #         else:
            #             normalized_ranked_features[i] = naive_mi
                    

        return normalized_ranked_features

    def _discrete_entropy(self, labels):
        """Calculates the entropy for a labeling.
        Parameters
        ----------
        labels : int array, shape = [n_samples]
            The labels
        Notes
        -----
        The logarithm used is the natural logarithm (base-e).
        """
        if len(labels) == 0:
            return 1.0
        label_idx = np.unique(labels, return_inverse=True)[1]
        pi = np.bincount(label_idx).astype(np.float64)
        pi = pi[pi > 0]
        pi_sum = np.sum(pi)
        # log(a / b) should be calculated as log(a) - log(b) for
        # possible loss of precision
        entropy = -np.sum((pi / pi_sum) * (np.log(pi) - log(pi_sum)))
        if entropy <= 0:
                raise exceptions.InvalidArgumentValueError('inputs has too many non-unique values or not enough values possibly')
        return entropy

    def _continuous_entropy(self, x):
        k = self.hyperparams['k']
        result = mutual_info_regression(x.values.reshape(-1, 1), x.values.reshape(-1, 1), [False], n_neighbors=k, random_state=self._random_seed)[0]
        # sorted_x = np.sort(x)

        # eps_distances = np.empty(x.shape[0])
        # k_all = np.full(x.shape, k - 1)
        # for i in range(x.shape[0]):
        #     eps = 0
        #     # need to prevent having an epsilon value of 0
        #     # as a fall back, increase k to find farther neighbours
        #     while eps == 0 and k_all[i] < x.shape[0]:
        #         k_all[i] += 1
        #         eps = self._eps(sorted_x, i, k_all[i])
        #     if eps == 0 and k_all[i] == x.shape[0]:
        #         raise exceptions.InvalidArgumentValueError('inputs has too many non-unique values or not enough values possibly')
        #     eps_distances[i] = eps

        # log_mean = np.mean(np.log(eps_distances))
        # if log_mean == float('-inf'):
        #     raise exceptions.InvalidArgumentValueError('inputs has too many non-unique values or not enough values possibly')

        # # this estimation is https://arxiv.org/pdf/cond-mat/0305641.pdf
        # # the KSG estimator
        # ksg_entropy = - np.mean(digamma(k_all)) + digamma(x.shape[0]) + log_mean
        # # a more naive estimator
        # # this estimation is from http://papers.neurips.cc/paper/3417-estimation-of-information-theoretic-measures-for-continuous-random-variables.pdf
        # naive_entropy = - np.mean(np.log(k_all/(x.shape[0] - 1) * gamma(1.5) / pow(3.14159, 0.5) * 1 / eps_distances)) - np.mean(digamma(k_all))
        return result

    def _eps(self, x, i, k):
        return 2 * abs(x[self._k_closest_neighbour(x, i, k)] - x[i])

    # assumes a is sorted
    def _k_closest_neighbour(self, a, i, k):
        length = len(a)
        j = 0
        if i == 0:
            l = -1
            r = i + 1
        elif i == length - 1:
            r = length
            l = i - 1
        else:
            r = i + 1
            l = i - 1
        kth_closest = i
        while j < k:
            if r == length:
                if l == -1:
                    if abs(a[0] - a[i]) > abs(a[length - 1] - a[i]):
                        return 0
                    return length - 1
                elif abs(a[r - 1] - a[i]) <= abs(a[l] - a[i]):
                    kth_closest = l
                    l -= 1
            elif l == -1 and abs(a[r] - a[i]) >= abs(a[0] - a[i]):
                kth_closest = r
                r += 1
            elif r < length and abs(a[r] - a[i]) <= abs(a[l] - a[i]):
                kth_closest = r
                r += 1
            elif l > -1 and abs(a[r] - a[i]) >= abs(a[l] - a[i]):
                kth_closest = l
                l -= 1
            j += 1
        return kth_closest
