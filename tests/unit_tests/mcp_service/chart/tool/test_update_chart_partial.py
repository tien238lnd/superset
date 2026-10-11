# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Partial-update semantics of ``config`` in update_chart: within one chart
type an omitted field keeps the saved value, a named field is written, and the
save and preview paths build the same form_data."""

import importlib
from collections.abc import Iterator
from typing import Any, get_args
from unittest.mock import Mock, patch

import pytest
from pydantic import BaseModel

from superset.mcp_service.chart.plugin import BaseChartPlugin, SHARED_UPDATE_FIELDS
from superset.mcp_service.chart.registry import get_registry
from superset.mcp_service.chart.schemas import ChartConfig, UpdateChartRequest
from superset.utils import json

update_chart_module = importlib.import_module(
    "superset.mcp_service.chart.tool.update_chart"
)

# Keys the preview path stamps for Explore; the saved chart carries them in
# its own columns instead of params.
_PREVIEW_STAMPS = ("slice_id", "slice_name", "datasource")

_REGION_FILTER = {
    "clause": "WHERE",
    "expressionType": "SIMPLE",
    "subject": "region",
    "operator": "==",
    "comparator": "North",
}

SAVED_AG_GRID_TABLE = {
    "viz_type": "ag-grid-table",
    "query_mode": "raw",
    "all_columns": ["region", "sales"],
    "columns": ["region", "sales"],
    "include_time": False,
    "order_desc": True,
    "row_limit": 50,
    "adhoc_filters": [_REGION_FILTER],
    "color_scheme": "lyftColors",
    "page_length": 25,
    "table_timestamp_format": "%Y",
}
TABLE_COLUMNS = [{"name": "region"}, {"name": "sales"}]

SAVED_BAR = {
    "viz_type": "echarts_timeseries_bar",
    "x_axis": "region",
    "metrics": ["count"],
    "adhoc_filters": [_REGION_FILTER],
    "color_scheme": "lyftColors",
    "show_legend": False,
    "legendOrientation": "bottom",
    "show_value": True,
    "row_limit": 25,
}
XY_ROLES = {"x": {"name": "region"}, "y": [{"name": "count", "saved_metric": True}]}

SAVED_PIE = {
    "viz_type": "pie",
    "groupby": ["region"],
    "metric": "count",
    "donut": True,
    "show_labels": False,
    "label_type": "percent",
    "sort_by_metric": False,
    "show_legend": False,
    "show_total": True,
    "labels_outside": False,
    "row_limit": 20,
}
PIE_ROLES = {
    "dimension": {"name": "region"},
    "metric": {"name": "count", "saved_metric": True},
}

# Plugins that merge an update in their own merge_update_form_data.
OWN_MERGE_PLUGINS = {
    "gauge": "merges through _GAUGE_FORM_DATA_FIELD_MAP",
    "treemap_v2": "merges through _TREEMAP_PRESENTATION_KEYS",
    "gantt": "merges through merge_gantt_update_form_data",
}


@pytest.fixture(autouse=True)
def no_dataset_lookup() -> Iterator[None]:
    """Mapping looks the dataset up only for temporal bindings; these charts
    have none."""
    with (
        patch(
            "superset.mcp_service.chart.chart_utils._find_dataset_by_id_or_uuid",
            return_value=None,
        ),
        patch(
            "superset.mcp_service.chart.chart_utils.is_column_truly_temporal",
            return_value=False,
        ),
    ):
        yield


def _both_paths(saved: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Resolve and map ``config`` as update_chart does, on the save path and
    the preview path, and return the form_data after checking they agree."""
    request = UpdateChartRequest.model_validate({"identifier": 42, "config": config})
    assert request.config is not None
    plugin = get_registry().get(request.config.chart_type, include_disabled=True)
    assert plugin is not None
    parsed_config = plugin.resolve_update_config(
        request.config, saved, dataset_rebind=False
    )
    chart = Mock(id=42, datasource_id=3, slice_name="Saved", params=json.dumps(saved))
    payload = update_chart_module._build_update_payload(
        request, chart, parsed_config=parsed_config
    )
    preview = update_chart_module._build_preview_form_data(
        request, chart, parsed_config=parsed_config
    )
    assert isinstance(payload, dict)
    assert isinstance(preview, dict)
    saved_params = json.loads(payload["params"])
    assert {
        key: value for key, value in preview.items() if key not in _PREVIEW_STAMPS
    } == {key: value for key, value in saved_params.items() if key != "datasource"}
    assert payload["viz_type"] == saved_params["viz_type"]
    return saved_params


def test_ag_grid_table_keeps_its_viz_type_and_controls_when_not_named() -> None:
    form_data = _both_paths(
        SAVED_AG_GRID_TABLE,
        {"chart_type": "table", "columns": TABLE_COLUMNS, "row_limit": 80},
    )
    assert form_data == {**SAVED_AG_GRID_TABLE, "row_limit": 80}


def test_switching_table_to_ag_grid_keeps_everything_else() -> None:
    saved = {**SAVED_AG_GRID_TABLE, "viz_type": "table"}
    form_data = _both_paths(
        saved,
        {"chart_type": "table", "viz_type": "ag-grid-table", "columns": TABLE_COLUMNS},
    )
    assert form_data == {**saved, "viz_type": "ag-grid-table"}


def test_xy_keeps_its_kind_when_not_named() -> None:
    form_data = _both_paths(SAVED_BAR, {"chart_type": "xy", **XY_ROLES})
    assert form_data["viz_type"] == "echarts_timeseries_bar"
    assert form_data["show_value"] is True
    assert form_data["show_legend"] is False
    assert form_data["legendOrientation"] == "bottom"


def test_changing_xy_kind_keeps_filters_colors_and_settings() -> None:
    form_data = _both_paths(SAVED_BAR, {"chart_type": "xy", "kind": "line", **XY_ROLES})
    assert form_data["viz_type"] == "echarts_timeseries_line"
    assert _REGION_FILTER in form_data["adhoc_filters"]
    assert form_data["color_scheme"] == "lyftColors"
    assert form_data["row_limit"] == 25
    assert form_data["show_legend"] is False


def test_named_false_turns_a_saved_control_off() -> None:
    form_data = _both_paths(
        SAVED_BAR,
        {"chart_type": "xy", "kind": "bar", "show_value": False, **XY_ROLES},
    )
    # The mapper writes show_value only when it is on, so naming it false
    # drops the saved value.
    assert "show_value" not in form_data


def test_omitted_filters_keep_the_saved_filters() -> None:
    form_data = _both_paths(SAVED_BAR, {"chart_type": "xy", "kind": "bar", **XY_ROLES})
    assert form_data["adhoc_filters"] == [_REGION_FILTER]


def test_null_filters_clear_the_saved_filters() -> None:
    form_data = _both_paths(
        SAVED_BAR,
        {"chart_type": "xy", "kind": "bar", "filters": None, **XY_ROLES},
    )
    assert "adhoc_filters" not in form_data


def test_named_nested_field_leaves_its_siblings_alone() -> None:
    form_data = _both_paths(
        SAVED_BAR,
        {"chart_type": "xy", "legend": {"position": "top"}, **XY_ROLES},
    )
    assert form_data["legendOrientation"] == "top"
    # legend.show was not named, so the saved hidden legend stays hidden.
    assert form_data["show_legend"] is False


def test_pie_keeps_saved_presentation_when_one_field_changes() -> None:
    form_data = _both_paths(
        SAVED_PIE, {"chart_type": "pie", "row_limit": 10, **PIE_ROLES}
    )
    for key in (
        "donut",
        "show_labels",
        "label_type",
        "sort_by_metric",
        "show_legend",
        "show_total",
        "labels_outside",
    ):
        assert form_data[key] == SAVED_PIE[key], key
    assert form_data["row_limit"] == 10


@pytest.mark.parametrize("row_limit", [5, 100])
def test_named_field_is_written_even_when_it_equals_the_default(
    row_limit: int,
) -> None:
    # 100 is PieChartConfig's default row_limit.
    form_data = _both_paths(
        SAVED_PIE, {"chart_type": "pie", "row_limit": row_limit, **PIE_ROLES}
    )
    assert form_data["row_limit"] == row_limit


def test_omitted_field_the_saved_chart_never_had_gets_the_mapper_default() -> None:
    """#44188: a key the saved chart lacks takes the mapped default, as for a
    new chart. Only a value the saved chart holds is kept."""
    saved = {key: value for key, value in SAVED_PIE.items() if key != "outerRadius"}
    form_data = _both_paths(saved, {"chart_type": "pie", **PIE_ROLES})
    assert form_data["outerRadius"] == 70


def test_changing_chart_type_does_not_inherit_the_old_controls() -> None:
    form_data = _both_paths(
        SAVED_PIE, {"chart_type": "table", "columns": TABLE_COLUMNS}
    )
    assert form_data["viz_type"] == "table"
    for key in ("donut", "show_total", "labels_outside", "label_type"):
        assert key not in form_data, key


def test_big_number_keeps_its_trendline_when_not_named() -> None:
    saved = {
        "viz_type": "big_number",
        "metric": "count",
        "granularity_sqla": "ds",
        "show_trend_line": True,
        "start_y_axis_at_zero": False,
        "compare_lag": 7,
        "time_format": "%Y",
        "subheader": "old",
    }
    form_data = _both_paths(
        saved,
        {
            "chart_type": "big_number",
            "metric": {"name": "count", "saved_metric": True},
            "subheader": "new",
        },
    )
    assert form_data == {**saved, "subheader": "new"}


def _config_models() -> dict[str, type[BaseModel]]:
    """Return each chart type's config model, keyed by chart_type."""
    union = get_args(get_args(ChartConfig)[0])
    models = {}
    for model in union:
        (chart_type,) = get_args(model.model_fields["chart_type"].annotation)
        models[chart_type] = model
    return models


def _nested_field_names(model: type[BaseModel], field: str) -> set[str]:
    nested = [
        arg
        for arg in get_args(model.model_fields[field].annotation)
        if isinstance(arg, type) and issubclass(arg, BaseModel)
    ]
    return set(nested[0].model_fields) if nested else set()


def test_every_config_field_has_an_update_rule() -> None:
    """Each config field is in its plugin's update_form_data_keys or carries a
    stated reason, so a new field cannot silently fall back to the mapper's
    default on every update."""
    registry = get_registry()
    models = _config_models()
    missing: dict[str, list[str]] = {}
    unknown: dict[str, list[str]] = {}
    for chart_type, model in models.items():
        plugin = registry.get(chart_type, include_disabled=True)
        assert plugin is not None, chart_type
        if chart_type in OWN_MERGE_PLUGINS:
            assert (
                type(plugin).merge_update_form_data
                is not BaseChartPlugin.merge_update_form_data
            ), chart_type
            continue
        keys = plugin.update_form_data_keys
        exempt = {**SHARED_UPDATE_FIELDS, **plugin.update_form_data_exempt}
        listed = {path.split(".")[0] for path in keys}
        if absent := sorted(set(model.model_fields) - listed - set(exempt)):
            missing[chart_type] = absent
        stale = [
            path
            for path in keys
            if path.split(".")[0] not in model.model_fields
            or (
                "." in path
                and path.split(".")[1]
                not in _nested_field_names(model, path.split(".")[0])
            )
        ]
        if stale:
            unknown[chart_type] = stale
    assert not missing, (
        "Add these config fields to the plugin's update_form_data_keys, or to "
        f"update_form_data_exempt with the reason: {missing}"
    )
    assert not unknown, f"update_form_data_keys names no such field: {unknown}"
