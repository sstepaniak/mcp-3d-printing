"""Central definitions for the public MCP tool surface."""

# Tool name constants
CALL_AUTODESK_API = "call_autodesk_api"
EXECUTE_PYTHON = "execute_python"
CAPTURE_VIEWPORT = "capture_viewport"
FETCH_API_DOCUMENTATION = "fetch_api_documentation"
FETCH_ONLINE_DOCUMENTATION = "fetch_online_documentation"
FETCH_DESIGN_GUIDE = "fetch_design_guide"
SAVE_SCRIPT = "save_script"
LOAD_SCRIPT = "load_script"
LIST_SCRIPTS = "list_scripts"
DELETE_SCRIPT = "delete_script"
GET_ACTIVE_SELECTION = "get_active_selection"
EXPORT_STL = "export_stl"
EXPORT_STEP = "export_step"
EXPORT_3MF = "export_3mf"
EXPORT_DXF = "export_dxf"
LIST_PARAMETERS = "list_parameters"
GET_PARAMETER = "get_parameter"
SET_PARAMETER = "set_parameter"
MEASURE_BODY = "measure_body"
CHECK_INTERFERENCE = "check_interference"
LIST_COMPONENTS = "list_components"
GET_TIMELINE = "get_timeline"
SUPPRESS_FEATURE = "suppress_feature"
UNSUPPRESS_FEATURE = "unsuppress_feature"
ROLLBACK_TO = "rollback_to"
SAVE_VERSION = "save_version"
LIST_VERSIONS = "list_versions"

# Resource constants
RESOURCE_URI = "fusion://design-guide"
RESOURCE_NAME = "Autodesk Fusion Design Guide"
RESOURCE_DESCRIPTION = "Workflow guidance, API patterns, naming rules, and modeling habits for Autodesk Fusion."

# Each tool: {"name", "description", "inputSchema"}
TOOL_DEFINITIONS = [
    {
        "name": CALL_AUTODESK_API,
        "description": (
            "Execute a generic Autodesk Fusion API call. "
            "Resolve a dotted API path, invoke it with args/kwargs, and optionally store the result.\n\n"
            "Path shortcuts: app, ui, design, rootComponent, $stored_name\n\n"
            "Constructors accepted in args/kwargs: Point3D, Vector3D, Point2D, "
            "ValueInput, ObjectCollection, Matrix3D\n\n"
            'Example: {"api_path": "rootComponent.sketches.add", '
            '"args": ["rootComponent.xYConstructionPlane"], "remember_as": "my_sketch"}'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "api_path": {
                    "type": "string",
                    "description": (
                        "Dotted path to Fusion API method/property "
                        "(e.g. 'rootComponent.sketches.add'). "
                        "Shortcuts: app, ui, design, rootComponent, $stored_var"
                    ),
                },
                "args": {
                    "type": "array",
                    "items": {},
                    "description": (
                        "Positional arguments. Can be literals, API paths, "
                        "$references, or constructors like "
                        '{"type": "Point3D", "x": 0, "y": 0, "z": 0}'
                    ),
                },
                "kwargs": {
                    "type": "object",
                    "description": "Keyword arguments for the API call",
                },
                "remember_as": {
                    "type": "string",
                    "description": "Store the result with this name for later use via $name",
                },
                "return_properties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Which properties to return from the result object",
                },
                "description": {
                    "type": "string",
                    "description": "Short description of what this API call does",
                },
            },
        },
    },
    {
        "name": EXECUTE_PYTHON,
        "description": (
            "Run Python code inside the active Fusion 360 session with access "
            "to the full SDK. Variables persist across calls within the same session_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID for persistent variables (default: 'default')",
                },
                "persistent": {
                    "type": "boolean",
                    "description": "Whether to persist session variables (default: true)",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Short description of what the code does, "
                        "shown in Fusion console"
                    ),
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": CAPTURE_VIEWPORT,
        "description": "Capture the current Fusion 360 viewport as a PNG image.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "width": {
                    "type": "integer",
                    "description": "Image width in pixels (default: 800)",
                },
                "height": {
                    "type": "integer",
                    "description": "Image height in pixels (default: 600)",
                },
            },
        },
    },
    {
        "name": FETCH_API_DOCUMENTATION,
        "description": (
            "Search live Fusion API metadata through runtime introspection. "
            "Returns scored results with class overviews, properties, "
            "and function signatures."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": (
                        "Search term (e.g. 'BRepBody', 'sketches', "
                        "'adsk.fusion.Sketch.add')"
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Search category: class_name, member_name, description, or all"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 3)",
                },
            },
            "required": ["search_term"],
        },
    },
    {
        "name": FETCH_ONLINE_DOCUMENTATION,
        "description": (
            "Fetch Autodesk cloudhelp documentation for a specific "
            "Fusion API class or member."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_name": {
                    "type": "string",
                    "description": "API class name (e.g. 'BRepBody', 'Sketch')",
                },
                "member_name": {
                    "type": "string",
                    "description": "Optional member name (e.g. 'add', 'name')",
                },
            },
            "required": ["class_name"],
        },
    },
    {
        "name": FETCH_DESIGN_GUIDE,
        "description": (
            "Read the bundled Fusion design guide with workflow guidance, "
            "API patterns, naming rules, and modeling habits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": SAVE_SCRIPT,
        "description": "Save a reusable Python script to the user scripts directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Script filename (e.g. 'my_script.py')",
                },
                "code": {
                    "type": "string",
                    "description": "Python code to save",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description of the script",
                },
            },
            "required": ["filename", "code"],
        },
    },
    {
        "name": LOAD_SCRIPT,
        "description": "Load a previously saved Python script by filename.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Script filename to load",
                },
            },
            "required": ["filename"],
        },
    },
    {
        "name": LIST_SCRIPTS,
        "description": (
            "List all saved Python scripts with metadata "
            "(filename, size, modified date)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": DELETE_SCRIPT,
        "description": "Delete a saved Python script by filename.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Script filename to delete",
                },
            },
            "required": ["filename"],
        },
    },
    {
        "name": GET_ACTIVE_SELECTION,
        "description": (
            "Get the objects currently selected by the user in the Fusion 360 viewport. "
            "Returns detailed info per item (type, name, entityToken, parent component, "
            "and type-specific properties like area, volume, material). "
            "Each selected object is stored as $selection_0, $selection_1, etc. "
            "for use in follow-up API calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": EXPORT_STL,
        "description": (
            "Export a body or component from the active Fusion 360 design as a binary STL file. "
            "If component_name is omitted, the first solid visible body is exported. "
            "The output path defaults to a system temp file when not specified. "
            "Returns file_path and file_size_bytes. "
            "Note: the STL unit reflects the design's active unit system; "
            "the units parameter is recorded in the response but does not override Fusion's settings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component_name": {
                    "type": "string",
                    "description": (
                        "Name of the body or component to export. "
                        "Body names are matched first, then component names. "
                        "Omit to export the first solid visible body."
                    ),
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Absolute path for the output .stl file. "
                        "A temp file is created when omitted."
                    ),
                },
                "units": {
                    "type": "string",
                    "description": "Annotation for the intended unit system (default: 'mm'). Does not override Fusion's design units.",
                },
            },
        },
    },
    {
        "name": EXPORT_STEP,
        "description": (
            "Export a component (or the entire design) from the active Fusion 360 design as a STEP file. "
            "If component_name is omitted, the root component (whole design) is exported. "
            "Returns file_path and file_size_bytes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component_name": {
                    "type": "string",
                    "description": (
                        "Name of the component to export. "
                        "Omit to export the entire design."
                    ),
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Absolute path for the output .step file. "
                        "A temp file is created when omitted."
                    ),
                },
            },
        },
    },
    {
        "name": EXPORT_3MF,
        "description": (
            "Export the entire active Fusion 360 design as a 3MF file. "
            "3MF is always rooted at the design's root component. "
            "Returns file_path and file_size_bytes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_path": {
                    "type": "string",
                    "description": (
                        "Absolute path for the output .3mf file. "
                        "A temp file is created when omitted."
                    ),
                },
            },
        },
    },
    {
        "name": EXPORT_DXF,
        "description": (
            "Export a named sketch from the active Fusion 360 design as a DXF file. "
            "The sketch is searched in the root component first, then recursively through "
            "all sub-components. sketch_name is required. "
            "Returns file_path and file_size_bytes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sketch_name": {
                    "type": "string",
                    "description": "Name of the sketch to export as DXF.",
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Absolute path for the output .dxf file. "
                        "A temp file is created when omitted."
                    ),
                },
            },
            "required": ["sketch_name"],
        },
    },
    # -----------------------------------------------------------------------
    # Parameter tools
    # -----------------------------------------------------------------------
    {
        "name": LIST_PARAMETERS,
        "description": (
            "Return all user parameters in the active Fusion 360 design. "
            "Each parameter includes name, expression (e.g. '5 mm'), raw value "
            "(Fusion internal units: cm for lengths, rad for angles), unit string, "
            "and comment. Returns an empty list if no user parameters exist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": GET_PARAMETER,
        "description": (
            "Return full details for one named user parameter. "
            "Raises a clear error with available names if the parameter is not found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact name of the user parameter to retrieve.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": SET_PARAMETER,
        "description": (
            "Modify a user parameter value and trigger a model update. "
            "The value is treated as being in the parameter's current unit (or the "
            "explicitly provided unit). Fusion evaluates the expression and propagates "
            "the change through the parametric timeline. "
            "Returns name, old_value, new_value, unit, old_expression, new_expression."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the user parameter to modify.",
                },
                "value": {
                    "type": "number",
                    "description": (
                        "New numeric value in the parameter's current unit "
                        "(or the unit specified by the unit field)."
                    ),
                },
                "unit": {
                    "type": "string",
                    "description": (
                        "Unit override for the new value (e.g. 'mm', 'in', 'deg'). "
                        "Defaults to the parameter's existing unit when omitted."
                    ),
                },
            },
            "required": ["name", "value"],
        },
    },
    # -----------------------------------------------------------------------
    # Analysis tools
    # -----------------------------------------------------------------------
    {
        "name": MEASURE_BODY,
        "description": (
            "Return physical measurements for a named body (or the first solid visible body "
            "if body_name is omitted). "
            "Returns: volume_mm3, surface_area_mm2, bounding_box_mm (x/y/z extents), "
            "center_of_mass_mm (x/y/z). All values are in millimetres or mm³/mm²."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "body_name": {
                    "type": "string",
                    "description": (
                        "Name of the BRepBody to measure. "
                        "Searched in root component then all sub-components. "
                        "Omit to measure the first solid visible body."
                    ),
                },
            },
        },
    },
    {
        "name": CHECK_INTERFERENCE,
        "description": (
            "Check whether two named components physically interfere (overlap). "
            "Returns interferes (bool) and interference_volume_mm3 (summed volume of "
            "all overlapping regions in mm³, or null if none or not computable)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component_a": {
                    "type": "string",
                    "description": "Name of the first component to check.",
                },
                "component_b": {
                    "type": "string",
                    "description": "Name of the second component to check.",
                },
            },
            "required": ["component_a", "component_b"],
        },
    },
    {
        "name": LIST_COMPONENTS,
        "description": (
            "Return a flat tree of all components and bodies in the active Fusion 360 design. "
            "Each entry has: name, type ('component' or 'body'), parent (component name or null "
            "for the root), is_visible, and for bodies also is_solid. "
            "Useful for discovering what bodies and components are available before calling "
            "other tools."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # -----------------------------------------------------------------------
    # History and version tools
    # -----------------------------------------------------------------------
    {
        "name": GET_TIMELINE,
        "description": (
            "Return the ordered list of timeline features in the active Fusion 360 design. "
            "Each entry includes index, name, type (e.g. ExtrudeFeature), is_suppressed, "
            "and is_rolled_back (True when the feature is beyond the current rollback marker). "
            "Also returns marker_position (the index of the first rolled-back feature). "
            "Returns an empty list if the design is in Direct Modelling mode or has no features."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": SUPPRESS_FEATURE,
        "description": (
            "Suppress the parametric timeline feature at the given zero-based index. "
            "Fusion removes the feature from the model computation without deleting it. "
            "Returns index, name, and suppressed=True. "
            "Raises a clear error if index is out of range."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "Zero-based index of the timeline feature to suppress.",
                },
            },
            "required": ["index"],
        },
    },
    {
        "name": UNSUPPRESS_FEATURE,
        "description": (
            "Unsuppress (re-enable) the parametric timeline feature at the given zero-based index. "
            "Returns index, name, and suppressed=False. "
            "Raises a clear error if index is out of range."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "Zero-based index of the timeline feature to unsuppress.",
                },
            },
            "required": ["index"],
        },
    },
    {
        "name": ROLLBACK_TO,
        "description": (
            "Move the timeline rollback marker to the given index (non-destructive). "
            "After this call, features 0 … index-1 are active; features at index and beyond "
            "are rolled back (not computed). Pass index equal to the total feature count to "
            "roll fully forward. "
            "Returns rolled_back_to_index and the name of the feature at the new boundary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": (
                        "Zero-based index where the rollback marker is placed. "
                        "Features before this index are active; features at or after are rolled back. "
                        "Use get_timeline first to see valid index values."
                    ),
                },
            },
            "required": ["index"],
        },
    },
    {
        "name": SAVE_VERSION,
        "description": (
            "Save a named version of the active document. "
            "Cloud documents: calls Document.save(description) to create a new numbered version; "
            "returns version_type='cloud' and the version ID. "
            "Local or unsaved documents: exports a .f3d archive with a datestamped filename "
            "to ~/Documents (or the system temp directory as fallback); "
            "returns version_type='local' and the full file path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Human-readable description attached to the saved version.",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": LIST_VERSIONS,
        "description": (
            "Return saved versions of the active document. "
            "Cloud documents: enumerates version history via DataFile.versions; "
            "falls back to current-version info if full history is inaccessible. "
            "Local or unsaved documents: returns an empty list (local .f3d exports "
            "have no version index). "
            "Each entry includes index, name, description, timestamp, version_id, is_latest."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

_TOOL_NAMES = {t["name"] for t in TOOL_DEFINITIONS}


def build_tool_handlers(
    *,
    generic_api_call,
    run_python,
    capture_viewport,
    fetch_api_documentation,
    fetch_online_documentation,
    fetch_design_guide,
    save_script,
    load_script,
    list_scripts,
    delete_script,
    get_active_selection,
    export_stl,
    export_step,
    export_3mf,
    export_dxf,
    list_parameters,
    get_parameter,
    set_parameter,
    measure_body,
    check_interference,
    list_components,
    get_timeline,
    suppress_feature,
    unsuppress_feature,
    rollback_to,
    save_version,
    list_versions,
):
    """Build a dict mapping tool name to handler function.

    All 27 tool names must have a corresponding handler. A RuntimeError
    is raised if the handler keys don't match TOOL_DEFINITIONS.
    """
    handlers = {
        CALL_AUTODESK_API: generic_api_call,
        EXECUTE_PYTHON: run_python,
        CAPTURE_VIEWPORT: capture_viewport,
        FETCH_API_DOCUMENTATION: fetch_api_documentation,
        FETCH_ONLINE_DOCUMENTATION: fetch_online_documentation,
        FETCH_DESIGN_GUIDE: fetch_design_guide,
        SAVE_SCRIPT: save_script,
        LOAD_SCRIPT: load_script,
        LIST_SCRIPTS: list_scripts,
        DELETE_SCRIPT: delete_script,
        GET_ACTIVE_SELECTION: get_active_selection,
        EXPORT_STL: export_stl,
        EXPORT_STEP: export_step,
        EXPORT_3MF: export_3mf,
        EXPORT_DXF: export_dxf,
        LIST_PARAMETERS: list_parameters,
        GET_PARAMETER: get_parameter,
        SET_PARAMETER: set_parameter,
        MEASURE_BODY: measure_body,
        CHECK_INTERFERENCE: check_interference,
        LIST_COMPONENTS: list_components,
        GET_TIMELINE: get_timeline,
        SUPPRESS_FEATURE: suppress_feature,
        UNSUPPRESS_FEATURE: unsuppress_feature,
        ROLLBACK_TO: rollback_to,
        SAVE_VERSION: save_version,
        LIST_VERSIONS: list_versions,
    }
    if set(handlers) != _TOOL_NAMES:
        raise RuntimeError(f"Handler registry mismatch: {set(handlers) ^ _TOOL_NAMES}")
    return handlers
