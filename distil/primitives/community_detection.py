import logging
import os

from d3m import container, utils
from d3m.metadata import base as metadata_base, hyperparams, params
from d3m.primitive_interfaces import base
from d3m.primitive_interfaces.base import CallResult
from d3m.primitive_interfaces.supervised_learning import PrimitiveBase
from distil.modeling.community_detection import CommunityDetection as _CommunityDetection
from distil.utils import CYTHON_DEP

__all__ = ('CommunityDetection',)

logger = logging.getLogger(__name__)


class Hyperparams(hyperparams.Hyperparams):
    pass

class Params(params.Params):
    model: _CommunityDetection
    target_col: str


class DistilCommunityDetectionPrimitive(PrimitiveBase[container.List, container.DataFrame, Params, Hyperparams]):
    """
    A primitive that wraps a null model handling of community detection.
    """
    metadata = metadata_base.PrimitiveMetadata(
        {
            'id': '064cec55-39dd-45b7-a663-50d3e17e0c42',
            'version': '0.1.1',
            'name': "CommunityDetection",
            'python_path': 'd3m.primitives.community_detection.community_detection.DistilCommunityDetection',
            'source': {
                'name': 'Distil',
                'contact': 'mailto:cbethune@uncharted.software',
                'uris': [
                    'https://github.com/uncharted-distil/distil-primitives/distil/primitives/community_detection.py',
                    'https://github.com/uncharted-distil/distil-primitives',
                ],
            },
            'installation': [CYTHON_DEP, {
                'type': metadata_base.PrimitiveInstallationType.PIP,
                'package_uri': 'git+https://github.com/uncharted-distil/distil-primitives.git@{git_commit}#egg=distil-primitives'.format(
                    git_commit=utils.current_git_commit(os.path.dirname(__file__)),
                ),
            }],
            'algorithm_types': [
                metadata_base.PrimitiveAlgorithmType.ARRAY_SLICING,
            ],
            'primitive_family': metadata_base.PrimitiveFamily.COMMUNITY_DETECTION,
        },
    )

    def __init__(self, *,
                 hyperparams: Hyperparams,
                 random_seed: int = 0) -> None:

        super().__init__(hyperparams=hyperparams, random_seed=random_seed)
        self._model = _CommunityDetection(overlapping=False)
        self._target_col = ""

    def set_training_data(self, *, inputs: container.List, outputs: container.DataFrame) -> None:
        self._inputs = inputs
        self._outputs = outputs
        self._target_col = outputs.columns[0]

    def fit(self, *, timeout: float = None, iterations: int = None) -> CallResult[None]:
        logger.debug(f'Fitting {__name__}')

        X_train, y_train, U_train = self._inputs
        X_train = X_train.value
        y_train = y_train.squeeze()
        self._model.fit(X_train, y_train, U_train)

        return CallResult(None)

    def produce(self, *, inputs: container.List, timeout: float = None, iterations: int = None) -> CallResult[container.DataFrame]:
        logger.debug(f'Producing {__name__}')

        X_train, _, _ = inputs
        X_train = X_train.value
        result = self._model.predict(X_train)

        # create dataframe to hold d3mIndex and result
        result_df = container.DataFrame({X_train.index.name: X_train.index, self._target_col: result})

        # mark the semantic types on the dataframe
        result_df.metadata = result_df.metadata.add_semantic_type((metadata_base.ALL_ELEMENTS, 0), 'https://metadata.datadrivendiscovery.org/types/PrimaryKey')
        result_df.metadata = result_df.metadata.add_semantic_type((metadata_base.ALL_ELEMENTS, 1), 'https://metadata.datadrivendiscovery.org/types/PredictedTarget')

        return base.CallResult(result_df)

    def get_params(self) -> Params:
        return Params(
            target_col=self._target_col,
            model=self._model
        )

    def set_params(self, *, params: Params) -> None:
        self._model = params['model']
        self._target_col = params['target_col']
        return
