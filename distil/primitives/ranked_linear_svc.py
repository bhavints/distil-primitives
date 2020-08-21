import logging
import os
from typing import List

import numpy as np
import pandas as pd
from sklearn.svm import LinearSVC
from scipy.stats import rankdata
from d3m import container, utils
from d3m.metadata import base as metadata_base, hyperparams, params
from d3m.primitive_interfaces import base
from d3m.primitive_interfaces.base import CallResult
from d3m.primitive_interfaces.supervised_learning import PrimitiveBase
from distil.utils import CYTHON_DEP
import version

__all__ = ("EnsembleForest",)

logger = logging.getLogger(__name__)


class Hyperparams(hyperparams.Hyperparams):
    penalty = hyperparams.Enumeration[str](
        default='l2',
        values=('l1', 'l2'),
        semantic_types=['https://metadata.datadrivendiscovery.org/types/ControlParameter'],
        description="The type of regularization for loss.",
    )
    loss = hyperparams.Enumeration[str](
        default='squared_hinge',
        values=('squared_hinge', 'hinge'),
        semantic_types=['https://metadata.datadrivendiscovery.org/types/ControlParameter'],
        description="The type of loss function.",
    )
    tolerance =  hyperparams.Hyperparameter[float](
        default=1e-4,
        semantic_types=['https://metadata.datadrivendiscovery.org/types/TuningParameter'],
        description="Tolerance for error. Aims to stop within th is tolerance",
    )

class Params(params.Params):
    model: LinearSVC
    target_cols: List[str]
    needs_fit: bool

class RankedLinearSVCPrimitive(
    PrimitiveBase[container.DataFrame, container.DataFrame, Params, Hyperparams]
):
    """
    Classifies data using an SVM with a linear kernel. It also provides a confidence rank for each classification,
    and provides a dataframe with those two columns.
    Parameters
    ----------
    inputs : A container.Dataframe with columns containing numeric data.
    Returns
    -------
    output : A DataFrame containing (target value, confidence ranking) tuples for each input row.
    """
    metadata = metadata_base.PrimitiveMetadata(
        {
            "id": "10d21bbe-9c58-4dc1-8f71-2b3834b71a5e",
            "version": version.__version__,
            "name": "DistilRankedLinearSVC",
            "python_path": "d3m.primitives.classification.support_vector_machine.DistilRankedLinearSVC",
            "source": {
                "name": "Distil",
                "contact": "mailto:cbethune@uncharted.software",
                "uris": [
                    "https://github.com/uncharted-distil/distil-primitives/distil/primitives/ranked_linear_svc.py",
                    "https://github.com/uncharted-distil/distil-primitives",
                ],
            },
            "installation": [CYTHON_DEP,
                {
                    "type": metadata_base.PrimitiveInstallationType.PIP,
                    "package_uri": "git+https://github.com/uncharted-distil/distil-primitives.git@{git_commit}#egg=distil-primitives".format(
                        git_commit=utils.current_git_commit(os.path.dirname(__file__)),
                    ),
                }
            ],
            "algorithm_types": [metadata_base.PrimitiveAlgorithmType.SUPPORT_VECTOR_MACHINE,],
            "primitive_family": metadata_base.PrimitiveFamily.CLASSIFICATION,
        },
    )

    def __init__(self, *, hyperparams: Hyperparams, random_seed: int = 0) -> None:

        super().__init__(hyperparams=hyperparams, random_seed=random_seed)
        self._model = LinearSVC(penalty=self.hyperparams['penalty'], loss=self.hyperparams['loss'], tol=self.hyperparams['tolerance'], random_state=random_seed)
        self._needs_fit = True

    def set_training_data(
        self, *, inputs: container.DataFrame, outputs: container.DataFrame
    ) -> None:
        self._inputs = inputs
        self._outputs = outputs
        self._needs_fit = True
        self._target_cols = []

    def fit(self, *, timeout: float = None, iterations: int = None) -> CallResult[None]:
        logger.debug(f"Fitting {__name__}")

        if self._needs_fit:
            self._model.fit(self._inputs.values, self._outputs.values)
            self._needs_fit = False
        return CallResult(None)

    def produce(
        self,
        *,
        inputs: container.DataFrame,
        timeout: float = None,
        iterations: int = None,
    ) -> CallResult[container.DataFrame]:

        logger.debug(f"Producing {__name__}")

        # force a fit it hasn't yet been done
        if self._needs_fit:
            self.fit()
        if not self._target_cols:
            self._target_cols = [self._outputs.columns[0]]

        # create dataframe to hold the result
        result = self._model.predict(inputs.values)
        confidences = self._model.decision_function(inputs.values)
        confidences_ranked = rankdata(confidences)

        result_df = container.DataFrame(
        {self._target_cols[0]: result, 'confidence': confidences_ranked}, generate_metadata=True
        )

        # mark the semantic types on the dataframe
        result_df.metadata = result_df.metadata.add_semantic_type(
            (metadata_base.ALL_ELEMENTS, 0),
            "https://metadata.datadrivendiscovery.org/types/PredictedTarget",
        )
        result_df.metadata = result_df.metadata.add_semantic_type(
            (metadata_base.ALL_ELEMENTS, 0),
            "http://schema.org/Integer",
        )
        # this is a hack, but str conversions on lists later on break things
        result_df.metadata = result_df.metadata.add_semantic_type(
            (metadata_base.ALL_ELEMENTS, 1),
            "https://metadata.datadrivendiscovery.org/types/PredictedTarget",
        )
        result_df.metadata = result_df.metadata.add_semantic_type(
            (metadata_base.ALL_ELEMENTS, 1),
            "https://metadata.datadrivendiscovery.org/types/Confidence",
        )
        result_df.metadata = result_df.metadata.add_semantic_type(
            (metadata_base.ALL_ELEMENTS, 1),
            "http://schema.org/Float",
        )

        return base.CallResult(result_df)

    def get_params(self) -> Params:
        return Params(
            model = self._model,
            needs_fit = self._needs_fit,
            target_cols = self._target_cols,
        )

    def set_params(self, *, params: Params) -> None:
        self._model = params['model']
        self._needs_fit = params['needs_fit']
        self._target_cols = params['target_cols']
        return