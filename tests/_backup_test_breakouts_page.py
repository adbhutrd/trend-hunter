"""Smoke tests for Streamlit UI pages.

Importing a Streamlit page executes ``st.*`` calls at module level, so we
patch the streamlit API before importing.  These tests only verify that
pages can be imported without raising.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def _mock_streamlit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Streamlit and related UI dependencies in sys.modules."""
    st = MagicMock()
    st.set_page_config = MagicMock()
    st.title = MagicMock()
    st.caption = MagicMock()
    st.sidebar = MagicMock()
    def _columns(n: int) -> list[MagicMock]:
        return [MagicMock() for _ in range(n)]

    st.columns = MagicMock(side_effect=_columns)
    st.metric = MagicMock()
    st.divider = MagicMock()
    st.subheader = MagicMock()
    st.info = MagicMock()
    st.warning = MagicMock()
    st.error = MagicMock()
    st.stop = MagicMock()
    st.bar_chart = MagicMock()
    st.selectbox = MagicMock(return_value=None)
    st.multiselect = MagicMock(return_value=[])
    st.slider = MagicMock(return_value=3)
    st.button = MagicMock(return_value=False)
    st.container = MagicMock(return_value=MagicMock())
    st.markdown = MagicMock()
    st.code = MagicMock()

    def _noop_decorator(*_args: object, **_kwargs: object):
        def _wrapper(fn: object) -> object:
            return fn

        return _wrapper

    st.cache_resource = _noop_decorator
    st.cache_data = _noop_decorator

    sa = MagicMock()
    sa.st_autorefresh = MagicMock()

    sag = MagicMock()
    sag.AgGrid = MagicMock()
    sag.GridOptionsBuilder = MagicMock()

    monkeypatch.setitem(sys.modules, "streamlit", st)
    monkeypatch.setitem(sys.modules, "streamlit_autorefresh", sa)
    monkeypatch.setitem(sys.modules, "st_aggrid", sag)


def test_breakouts_page_imports(_mock_streamlit: None) -> None:
    """The breakouts page should import without raising."""
    mod_name = "trend_hunter.ui.pages.3_🚀_breakouts"
    sys.modules.pop(mod_name, None)
    try:
        page = importlib.import_module(mod_name)
    finally:
        # Ensure the mocked module is not reused by later tests.
        sys.modules.pop(mod_name, None)
    assert page is not None
    assert hasattr(page, "_load_history")
    # Verify the page actually executed its top-level Streamlit calls.
    st = sys.modules["streamlit"]
    st.set_page_config.assert_called_once()
    st.title.assert_called_once()
    st_autorefresh = sys.modules["streamlit_autorefresh"]
    st_autorefresh.st_autorefresh.assert_called_once()
