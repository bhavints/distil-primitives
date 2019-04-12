from distutils.core import setup

setup(
    name='DistilPrimitives',
    version='0.1.0',
    description='Distil primitives as a single library (temporary)',
    packages=['exline'],
    keywords=['d3m_primitive'],
    install_requires=[
        'pandas>=0.23.4',
        'frozendict>=1.2',
        'scikit-learn>=0.20.2',
        'd3m==2019.4.4'
    ],
    dependency_links=[
        'git+https://gitlab.com/datadrivendiscovery/common-primitives.git#egg=common_primitives',
    ],
    entry_points={
        'd3m.primitives': [
            'data_transformation.imputer.ExlineSimpleImputer = exline.primitives.simple_imputer:SimpleImputerPrimitive',
            'data_transformation.data_cleaning.ExlineReplaceSingletons = exline.primitives.replace_singletons:ReplaceSingletonsPrimitive',
            'data_transformation.imputer.ExlineCategoricalImputer = exline.primitives.categorical_imputer:CategoricalImputerPrimitive',
            'data_transformation.data_cleaning.ExlineEnrichDates = exline.primitives.enrich_dates:EnrichDatesPrimitive',
            'learner.random_forest.ExlineEnsembleForest = exline.primitives.ensemble_forest:EnsembleForestPrimitive',
            'data_transformation.standard_scaler.ExlineStandardScaler = exline.primitives.standard_scaler:StandardScalerPrimitive',
            'data_transformation.one_hot_encoder.ExlineOneHotEncoder = exline.primitives.one_hot_encoder:OneHotEncoderPrimitive',
            'data_transformation.encoder.ExlineBinaryEncoder = exline.primitives.binary_encoder:BinaryEncoderPrimitive',
            'data_transformation.encoder.ExlineTextEncoder = exline.primitives.text_encoder:TextEncoderPrimitive',
            'data_transformation.column_parser.ExlineSimpleColumnParser = exline.primitives.simple_column_parser:SimpleColumnParserPrimitive',
            'data_transformation.missing_indicator.ExlineMissingIndicator = exline.primitives.missing_indicator:MissingIndicatorPrimitive',
            'data_transformation.data_cleaning.ExlineZeroColumnRemover = exline.primitives.zero_column_remover:ZeroColumnRemoverPrimitive'
        ],
    }
)