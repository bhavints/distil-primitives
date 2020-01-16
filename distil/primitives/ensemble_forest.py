import os
import sys
import logging
from typing import Set, List, Dict, Any, Optional

from d3m import container, utils
from d3m.metadata import base as metadata_base, hyperparams, params
from d3m.primitive_interfaces import base, transformer
from d3m.primitive_interfaces.supervised_learning import PrimitiveBase
from d3m.primitive_interfaces.base import CallResult

from distil.modeling.forest import ForestCV
from distil.modeling.metrics import classification_metrics, regression_metrics

from ShapExplainers import tree

import pandas as pd
import numpy as np

from common_primitives import denormalize, dataset_to_dataframe as DatasetToDataFrame
from distil.utils import CYTHON_DEP

__all__ = ("EnsembleForest",)

logger = logging.getLogger(__name__)


class Hyperparams(hyperparams.Hyperparams):
    metric = hyperparams.Hyperparameter[str](
        default="f1Macro",
        semantic_types=[
            "https://metadata.datadrivendiscovery.org/types/ControlParameter"
        ],
        description="The D3M scoring metric to use during the fit phase.  This can be any of the regression, classification or "
        + "clustering metrics.",
    )
    small_dataset_threshold = hyperparams.Hyperparameter[int](
        default=2000,
        semantic_types=[
            "https://metadata.datadrivendiscovery.org/types/ControlParameter"
        ],
        description="Controls the application of the 'small_dataset_fits' and 'large_dataset_fits' parameters - if the input dataset has "
        + "fewer rows than the threshold value, 'small_dateset_fits' will be used when fitting.  Otherwise, 'num_large_fits' is used.",
    )
    small_dataset_fits = hyperparams.Hyperparameter[int](
        default=1,
        semantic_types=[
            "https://metadata.datadrivendiscovery.org/types/ControlParameter"
        ],
        description="The number of random forests to fit when using small datasets.",
    )
    large_dataset_fits = hyperparams.Hyperparameter[int](
        default=1,
        semantic_types=[
            "https://metadata.datadrivendiscovery.org/types/ControlParameter"
        ],
        description="The number of random forests to fit when using large datasets.",
    )
    shap_approximation = hyperparams.Hyperparameter[bool](
        default=False,
        semantic_types=[
            "https://metadata.datadrivendiscovery.org/types/ControlParameter"
        ],
        description="Whether to calculate SHAP interpretability values using the Saabas approximation. This approximation samples over only one "
        + "permuation of feature values for each sample - that defined by the path along the decision tree. Thus, this approximation suffers "
        + "from inconsistency, which means that 'a model can change such that it relies more on a given feature, yet the importance estimate "
        + "assigned to that feature decreases' (Lundberg et al. 2019). Specifically, it will place too much weight on lower splits in the tree.",
    )
    shap_max_dataset_size = hyperparams.Hyperparameter[int](
        default=1500,
        semantic_types=[
            "https://metadata.datadrivendiscovery.org/types/ControlParameter"
        ],
        description="The maximum dataset size on which to apply SHAP interpretation to each sample individually. Otherwise, this number of samples will be"
        + "drawn from the data distribution after clustering (to approximate the distribution) and interpretation will only be applied to these"
        + "samples",
    )


class Params(params.Params):
    pass


class EnsembleForestPrimitive(
    PrimitiveBase[container.DataFrame, container.DataFrame, Params, Hyperparams]
):
    """
    Generates an ensemble of random forests, with the number of internal models created controlled by the size of the
    input dataframe.  It accepts a dataframe as input, and returns a dataframe consisting of prediction values only as output.
    Columns with string structural types are ignored.
    """

    metadata = metadata_base.PrimitiveMetadata(
        {
            "id": "e0ad06ce-b484-46b0-a478-c567e1ea7e02",
            "version": "0.3.0",
            "name": "EnsembleForest",
            "python_path": "d3m.primitives.learner.random_forest.DistilEnsembleForest",
            "source": {
                "name": "Distil",
                "contact": "mailto:cbethune@uncharted.software",
                "uris": [
                    "https://github.com/uncharted-distil/distil-primitives/distil/primitives/ensemble_forest.py",
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
            "algorithm_types": [metadata_base.PrimitiveAlgorithmType.RANDOM_FOREST,],
            "primitive_family": metadata_base.PrimitiveFamily.LEARNER,
        },
    )

    def __init__(self, *, hyperparams: Hyperparams, random_seed: int = 0) -> None:

        super().__init__(self, hyperparams=hyperparams, random_seed=random_seed)
        self._model = ForestCV(self.hyperparams["metric"])
        self._needs_fit = True
        self.label_map: Optional[Dict[int, str]] = None

    def __getstate__(self) -> dict:
        state = PrimitiveBase.__getstate__(self)
        state["models"] = self._model
        state["needs_fit"] = self._needs_fit
        return state

    def __setstate__(self, state: dict) -> None:
        PrimitiveBase.__setstate__(self, state)
        self._model = state["models"]
        self._needs_fit = True

    def _get_component_columns(
        self, output_df: container.DataFrame, source_col_index: int
    ) -> List[int]:
        # Component columns are all column which have as source the referenced
        # column index. This includes the aforementioned column index.
        component_cols = [source_col_index]

        # get the column name
        col_name = output_df.metadata.query(
            (metadata_base.ALL_ELEMENTS, source_col_index)
        )["name"]

        # get all columns which have this column as source
        for c in range(0, len(output_df.columns)):
            src = output_df.metadata.query((metadata_base.ALL_ELEMENTS, c))
            if "source_column" in src and src["source_column"] == col_name:
                component_cols.append(c)

        return component_cols

    def set_training_data(
        self, *, inputs: container.DataFrame, outputs: container.DataFrame
    ) -> None:
        # At this point anything that needed to be imputed should have been, so we'll
        # clear out any remaining NaN values as a last measure.

        # if we are doing a binary classification the outputs need to be integer classes.
        # label map is used to covert these back on produce.
        col = outputs.columns[0]
        if len(pd.factorize(outputs[col])[1]) <= 2:
            factor = pd.factorize(outputs[col])
            outputs = pd.DataFrame(factor[0], columns=[col])
            self.label_map = {k: v for k, v in enumerate(factor[1])}
        # remove nans from outputs, apply changes to inputs as well to ensure alignment
        self._outputs = outputs[
            outputs[col] != ""
        ].dropna()  # not in place because we don't want to modify passed input
        row_diff = outputs.shape[0] - self._outputs.shape[0]
        if row_diff != 0:
            logger.warn(f"Removed {row_diff} rows due to NaN values in target data.")
            self._inputs = inputs.loc[self._outputs.index, :]
        else:
            self._inputs = inputs

        # same in other direction
        inputs_rows = self._inputs.shape[0]
        self._inputs = (
            self._inputs.dropna()
        )  # not in place because because selection above doesn't create a copy
        row_diff = inputs_rows - self._inputs.shape[0]
        if row_diff != 0:
            logger.warn(f"Removed {row_diff} rows due to NaN values in training data.")
            self._outputs = self._outputs.loc[self._inputs.index, :]

        self._model.num_fits = (
            self.hyperparams["large_dataset_fits"]
            if self._inputs.shape[0] > self.hyperparams["small_dataset_threshold"]
            else self.hyperparams["small_dataset_fits"]
        )

        self._needs_fit = True

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

        # create dataframe to hold the result
        result = self._model.predict(inputs.values)
        if len(self._outputs.columns) > 1:
            result_df = container.DataFrame()
            for i, c in enumerate(self._outputs.columns):
                col = container.DataFrame({c: result[:, i]})
                result_df = pd.concat([result_df, col], axis=1)
            for c in range(result_df.shape[1]):
                result_df.metadata = result_df.metadata.add_semantic_type(
                    (metadata_base.ALL_ELEMENTS, c), "http://schema.org/Float"
                )
        else:
            result_df = container.DataFrame(
                {self._outputs.columns[0]: result}, generate_metadata=True
            )
        # if we mapped values earlier map them back.
        if self.label_map:
            # TODO label map will not work if there are multiple output columns.
            result_df[self._outputs.columns[0]] = result_df[
                self._outputs.columns[0]
            ].map(self.label_map)
        # mark the semantic types on the dataframe
        for i, _ in enumerate(result_df.columns):
            result_df.metadata = result_df.metadata.add_semantic_type(
                (metadata_base.ALL_ELEMENTS, i),
                "https://metadata.datadrivendiscovery.org/types/PredictedTarget",
            )

        logger.debug(f"\n{result_df}")
        return base.CallResult(result_df)

    def produce_feature_importances(
        self,
        *,
        inputs: container.DataFrame,
        timeout: float = None,
        iterations: int = None,
    ) -> CallResult[container.DataFrame]:
        logger.debug(f"Producing {__name__} feature weights")

        # force a fit it hasn't yet been done
        if self._needs_fit:
            self.fit()

        # extract the feature weights
        column_names = inputs.columns
        output = container.DataFrame(
            self._model.feature_importances().reshape((1, len(inputs.columns))),
            generate_metadata=True,
        )
        output.columns = inputs.columns
        for i in range(len(inputs.columns)):
            output.metadata = output.metadata.update_column(
                i, {"name": output.columns[i]}
            )

        return CallResult(output)

    def produce_shap_values(
        self,
        *,
        inputs: container.DataFrame,
        timeout: float = None,
        iterations: int = None,
    ) -> CallResult[container.DataFrame]:

        if self._needs_fit:
            self.fit()

        # don't want to produce SHAP predictions on train set because too computationally intensive
        if np.array_equal(inputs.values, self._inputs.values):
            logger.info(
                "Not producing SHAP interpretations on train set because of computational considerations"
            )
            return CallResult(container.DataFrame([]))

        # get the task type from the model instance
        task_type = self._model.mode

        # shap needs a pandas type dataframe, not d3 container type dataframe
        shap_df = pd.DataFrame(inputs)

        # create explanations
        exp = tree.Tree(
            self._model._models[0].model,
            X=shap_df,
            model_type="Random_Forest",
            task_type=task_type,
            max_dataset_size=self.hyperparams["shap_max_dataset_size"],
        )
        output_df = container.DataFrame(
            exp.produce_global(approximate=self.hyperparams["shap_approximation"]),
            generate_metadata=True,
        )
        output_df.reset_index(level=0, inplace=True)

        # metadata for columns
        component_cols: Dict[str, List[int]] = {}
        for c in range(0, len(output_df.columns)):
            col_dict = dict(inputs.metadata.query((metadata_base.ALL_ELEMENTS, c)))
            col_dict["structural_type"] = type(float)
            col_dict["name"] = output_df.columns[c]
            col_dict["semantic_types"] = (
                "https://metadata.datadrivendiscovery.org/types/Attribute",
            )
            output_df.metadata = output_df.metadata.update(
                (metadata_base.ALL_ELEMENTS, c), col_dict
            )
            if "source_column" in col_dict:
                src = col_dict["source_column"]
                if src not in component_cols:
                    component_cols[src] = []
                component_cols[src].append(c)

        # build the source column values and add them to the output
        for s, cc in component_cols.items():
            src_col = output_df.iloc[:, cc].apply(lambda x: sum(x), axis=1)
            src_col_index = len(output_df.columns)
            output_df.insert(src_col_index, s, src_col)
            output_df.metadata = output_df.metadata.add_semantic_type(
                (metadata_base.ALL_ELEMENTS, src_col_index),
                "https://metadata.datadrivendiscovery.org/types/Attribute",
            )

        df_dict = dict(output_df.metadata.query((metadata_base.ALL_ELEMENTS,)))
        df_dict_1 = dict(output_df.metadata.query((metadata_base.ALL_ELEMENTS,)))
        df_dict["dimension"] = df_dict_1
        df_dict_1["name"] = "columns"
        df_dict_1["semantic_types"] = (
            "https://metadata.datadrivendiscovery.org/types/TabularColumn",
        )
        df_dict_1["length"] = len(output_df.columns)
        output_df.metadata = output_df.metadata.update(
            (metadata_base.ALL_ELEMENTS,), df_dict
        )

        return CallResult(output_df)

    def get_params(self) -> Params:
        return Params()

    def set_params(self, *, params: Params) -> None:
        return
