import pytest
from suitcase.specfile import export


def test_export(tmp_path, example_data):
    documents = example_data()
    try:
        export(documents, tmp_path)
    except NotImplementedError:
        raise pytest.skip()
