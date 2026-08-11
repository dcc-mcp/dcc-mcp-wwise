import math
import re
from collections.abc import Mapping
from itertools import pairwise

from _validation import authoring_name, target_path
from dcc_mcp_core.skill import skill_entry, skill_success

from dcc_mcp_wwise.waapi import call_waapi

_PROPERTY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_SHAPES = {
    "Constant",
    "Linear",
    "Log3",
    "Log2",
    "Log1",
    "InvertedSCurve",
    "SCurve",
    "Exp1",
    "Exp2",
    "Exp3",
}


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


@skill_entry
def main(
    target,
    parameter_name,
    property,
    minimum,
    maximum,
    initial,
    points,
    parameter_folder="Gameplay",
    **_kwargs,
):
    target_object = target_path(target)
    parameter = authoring_name(parameter_name, "parameter_name").replace(" ", "_")
    folder = authoring_name(parameter_folder, "parameter_folder")
    property_name = str(property).strip()
    if not _PROPERTY.fullmatch(property_name):
        raise ValueError("property must be a valid Wwise property name")

    lower = _number(minimum, "minimum")
    upper = _number(maximum, "maximum")
    default = _number(initial, "initial")
    if lower >= upper or not lower <= default <= upper:
        raise ValueError("minimum, maximum, and initial must define a valid range")
    if not isinstance(points, list) or not 2 <= len(points) <= 64:
        raise ValueError("points must contain between 2 and 64 curve points")

    curve = []
    for point in points:
        if not isinstance(point, Mapping):
            raise ValueError("each curve point must be an object")
        x = _number(point.get("x"), "points.x")
        y = _number(point.get("y"), "points.y")
        shape = str(point.get("shape", "Linear")).strip()
        if not lower <= x <= upper or shape not in _SHAPES:
            raise ValueError("curve point range or shape is invalid")
        curve.append({"x": x, "y": y, "shape": shape})
    if any(left["x"] >= right["x"] for left, right in pairwise(curve)):
        raise ValueError("curve point x values must be strictly increasing")

    parameter_path = f"\\Game Parameters\\Default Work Unit\\{folder}\\{parameter}"
    parameter_result = call_waapi(
        "ak.wwise.core.object.set",
        {
            "objects": [
                {
                    "object": "\\Game Parameters\\Default Work Unit",
                    "children": [
                        {
                            "type": "Folder",
                            "name": folder,
                            "children": [
                                {
                                    "type": "GameParameter",
                                    "name": parameter,
                                    "@Min": lower,
                                    "@Max": upper,
                                    "@InitialValue": default,
                                }
                            ],
                        }
                    ],
                }
            ],
            "onNameConflict": "merge",
            "listMode": "append",
        },
        options={"return": ["id", "name", "type", "path"]},
    )
    rtpc_result = call_waapi(
        "ak.wwise.core.object.set",
        {
            "objects": [
                {
                    "object": target_object,
                    "@RTPC": [
                        {
                            "type": "RTPC",
                            "name": "",
                            "@PropertyName": property_name,
                            "@ControlInput": parameter_path,
                            "@Curve": {"type": "Curve", "points": curve},
                        }
                    ],
                }
            ],
            "onNameConflict": "merge",
            "listMode": "append",
        },
        options={"return": ["id", "name", "type", "@RTPC"]},
    )
    return skill_success(
        f"Configured {property_name} RTPC curve on the Wwise object.",
        target=target_object,
        parameter_path=parameter_path,
        property=property_name,
        point_count=len(curve),
        parameter_result=parameter_result,
        rtpc_result=rtpc_result,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
