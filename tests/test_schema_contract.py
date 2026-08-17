import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_NODE = shutil.which("node")
# Node cold-start can be slow on loaded Windows CI runners; 30 s still flaked
# (test_minus_elements_does_not_crash_on_zero_inputs timed out). Give it headroom.
_NODE_TIMEOUT_S = 120

# Keys in config_defaults.json with no param_definitions.json entry.
# These are ML-subsystem parameters not represented in the UI schema.
_KNOWN_UNDECLARED_DEFAULTS = frozenset(
    {
        "data_path",
        "deferrable_load_groups",
        "model_type",
        "num_lags",
        "perform_backtest",
        "sklearn_model",
        "split_date_delta",
        "var_model",
    }
)

# Keys whose default value type doesn't match their declared input type.
# Pre-existing mismatches outside the scope of the #876/#879 bug-wave.
# The battery_* / weight_battery_* block (#610): declared array.* because they
# accept per-battery lists, but their defaults stay bare scalars on purpose -
# a scalar broadcasts to every battery and keeps existing single-battery
# configs byte-identical (number_of_batteries defaults to 1).
_KNOWN_TYPE_MISMATCHES = frozenset(
    {
        "load_peak_hour_periods",  # declared array.time, default is dict
        "battery_discharge_power_max",
        "battery_charge_power_max",
        "battery_discharge_efficiency",
        "battery_charge_efficiency",
        "battery_nominal_energy_capacity",
        "battery_minimum_state_of_charge",
        "battery_maximum_state_of_charge",
        "battery_target_state_of_charge",
        "battery_stress_cost",
        "weight_battery_discharge",
        "weight_battery_charge",
        "battery_soc_deficit_threshold",
        "battery_soc_deficit_cost",
        "battery_soc_surplus_threshold",
        "battery_soc_surplus_cost",
        # #1042: declared array.string because they accept per-battery entity-id
        # lists at number_of_batteries > 1, but their defaults stay bare scalar
        # strings on purpose - a single sensor name is not broadcast to every
        # battery (unlike the numeric params above), so the scalar default is
        # only ever valid at N=1 and keeps existing single-battery configs
        # byte-identical.
        "sensor_power_battery",
        "sensor_battery_state_of_charge",
        # #540 Part B: declared array.* because they accept K independent
        # capacity/demand-charge components as a list, but their defaults stay
        # bare scalars on purpose - a scalar routes through the untouched K=1
        # legacy code path entirely (see Optimization.__init__), which a
        # single-element list does NOT (it opts into the generic K>1 path
        # instead - mathematically equivalent, but a different code path), so
        # keeping the published default a bare scalar is deliberate, exactly
        # mirroring the battery_* precedent above.
        "capacity_cost_per_kw",
        "capacity_charge_interval_timesteps",
    }
)

# Mapping from param_definitions "input" type to acceptable Python types for defaults.
_INPUT_TYPE_TO_PY_TYPES: dict[str, tuple[type, ...]] = {
    "int": (int, type(None)),
    "float": (int, float, type(None)),
    "string": (str, type(None)),
    "boolean": (bool,),
    "time": (str, type(None)),
    "select": (str, type(None)),
    "array.int": (list, type(None)),
    "array.float": (list, type(None)),
    "array.string": (list, type(None)),
    "array.boolean": (list, type(None)),
    "array.time": (list, type(None)),
    "array.array.float": (list, type(None)),
    "object": (dict, type(None)),
}


def _extract_switch_body(js_src: str, fn_name: str) -> str:
    """Return the body of the first switch block inside fn_name.

    Narrower than full-function extraction: brace-counts only the switch
    block, so extra braces in the surrounding function don't matter.
    """
    fn_m = re.search(rf"function\s+{re.escape(fn_name)}\s*\([^)]*\)\s*\{{", js_src)
    if not fn_m:
        raise AssertionError(f"function {fn_name!r} not found in JS source")
    sw_m = re.search(r"\bswitch\s*\([^)]+\)\s*\{", js_src[fn_m.end() :])
    if not sw_m:
        raise AssertionError(f"no switch statement found in function {fn_name!r}")
    start = fn_m.end() + sw_m.end()
    depth = 1
    i = start
    while i < len(js_src) and depth > 0:
        if js_src[i] == "{":
            depth += 1
        elif js_src[i] == "}":
            depth -= 1
        i += 1
    return js_src[start : i - 1]


def test_param_definitions_input_types_have_renderer_cases():
    """Every distinct 'input' value in param_definitions.json must have a
    matching case in the buildParamElement switch in configuration_script.js.

    Pre-#872 master would have failed with {'array.array.float', 'object'}
    missing from the switch.
    """
    pd_path = Path("src/emhass/static/data/param_definitions.json")
    pd = json.loads(pd_path.read_text(encoding="utf-8"))

    declared_inputs: set[str] = set()
    for section_entries in pd.values():
        for name, definition in section_entries.items():
            input_type = definition.get("input")
            assert input_type is not None, f"{name!r} has no 'input' field"
            declared_inputs.add(input_type)

    js_path = Path("src/emhass/static/configuration_script.js")
    js_src = js_path.read_text(encoding="utf-8")
    switch_body = _extract_switch_body(js_src, "buildParamElement")
    cases = set(re.findall(r'case\s+"([^"]+)"\s*:', switch_body))

    missing = declared_inputs - cases
    assert not missing, (
        f"param_definitions.json declares input types with no matching case in "
        f"buildParamElement: {sorted(missing)}. "
        f"Add a case in src/emhass/static/configuration_script.js or "
        f"remove the input type from param_definitions.json."
    )


def test_config_defaults_keys_match_param_definitions():
    """Every key in config_defaults.json must have a param_definitions.json entry,
    and its default value must be type-compatible with the declared 'input' type.

    Targeted check: an 'object' input with default value 'null' (the string)
    instead of JSON null will fail — this is the #876 footgun.

    Known pre-existing exceptions are in _KNOWN_UNDECLARED_DEFAULTS and
    _KNOWN_TYPE_MISMATCHES; new violations will cause this test to fail.
    """
    pd = json.loads(
        Path("src/emhass/static/data/param_definitions.json").read_text(encoding="utf-8")
    )
    cd = json.loads(Path("src/emhass/data/config_defaults.json").read_text(encoding="utf-8"))

    pd_flat: dict[str, str] = {}
    for section_entries in pd.values():
        for name, definition in section_entries.items():
            pd_flat[name] = definition["input"]

    # Check 1: every config_defaults key must appear in param_definitions
    new_undeclared = (set(cd.keys()) - set(pd_flat.keys())) - _KNOWN_UNDECLARED_DEFAULTS
    assert not new_undeclared, (
        f"config_defaults.json keys with no entry in param_definitions.json: "
        f"{sorted(new_undeclared)}. "
        f"Add an entry to src/emhass/static/data/param_definitions.json."
    )

    # Check 2: every input type declared in param_definitions must be mapped
    unmapped = set(pd_flat.values()) - set(_INPUT_TYPE_TO_PY_TYPES.keys())
    assert not unmapped, (
        f"param_definitions.json uses input types not in _INPUT_TYPE_TO_PY_TYPES: "
        f"{sorted(unmapped)}. Add them to the mapping in test_schema_contract.py."
    )

    # Check 3: each default value's Python type must match the declared input type
    mismatches: list[str] = []
    for key, default_value in cd.items():
        if key not in pd_flat or key in _KNOWN_TYPE_MISMATCHES:
            continue
        input_type = pd_flat[key]
        acceptable = _INPUT_TYPE_TO_PY_TYPES[input_type]
        # Targeted #876 footgun: string "null" for an object-typed param
        if input_type == "object" and default_value == "null":
            mismatches.append(
                f"{key}: default is the string 'null', not JSON null — "
                f"callers expecting a dict will crash (the #876 footgun)"
            )
            continue
        if not isinstance(default_value, acceptable):
            mismatches.append(
                f"{key}: default is {type(default_value).__name__} {default_value!r}, "
                f"declared input is {input_type!r} "
                f"(expected one of {[t.__name__ for t in acceptable]})"
            )

    assert not mismatches, (
        "config_defaults.json has values incompatible with their declared input type:\n  - "
        + "\n  - ".join(mismatches)
    )


# ---------------------------------------------------------------------------
# #880 / #904 regression tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def js_src() -> str:
    return Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def _build_param_fns(js_src: str) -> tuple[str, str]:
    """Extracted checkConfigParam + buildParamElement sources, shared across Node replay tests."""
    return (
        _extract_function_src(js_src, "checkConfigParam"),
        _extract_function_src(js_src, "buildParamElement"),
    )


def _extract_function_src(js_src: str, fn_name: str) -> str:
    """Extract a complete top-level ``[async] function <fn_name>(...) { ... }`` block."""
    fn_m = re.search(rf"(?:async\s+)?function\s+{re.escape(fn_name)}\s*\([^)]*\)\s*\{{", js_src)
    if not fn_m:
        raise AssertionError(f"function {fn_name!r} not found in JS source")
    brace_open = js_src.index("{", fn_m.start())
    depth = 1
    i = brace_open + 1
    while i < len(js_src) and depth > 0:
        if js_src[i] == "{":
            depth += 1
        elif js_src[i] == "}":
            depth -= 1
        i += 1
    return js_src[fn_m.start() : i]


def _extract_const_src(js_src: str, const_name: str) -> str:
    """Extract a complete top-level ``const <const_name> = ... ;`` statement."""
    m = re.search(rf"const\s+{re.escape(const_name)}\s*=", js_src)
    if not m:
        raise AssertionError(f"const {const_name!r} not found in JS source")
    end = js_src.index(";", m.start())
    return js_src[m.start() : end + 1]


def _run_node(script: str) -> subprocess.CompletedProcess:
    """Write *script* to a temp file and run it with Node.js."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        tmp = f.name
    try:
        return subprocess.run([_NODE, tmp], capture_output=True, text=True, timeout=_NODE_TIMEOUT_S)
    finally:
        os.unlink(tmp)


def test_minus_elements_no_undefined_variable(js_src):
    """minusElements must not reference ``parameter_definition_name``.

    On master this FAILS: the variable appears at two console.log paths
    (lines 556 and 563), causing an uncaught ReferenceError that aborts
    window.onload and leaves the whole config UI dead (#880).
    After fix: PASSES — renamed to ``param`` everywhere in the function.
    """
    fn_src = _extract_function_src(js_src, "minusElements")
    assert "parameter_definition_name" not in fn_src, (
        "minusElements references `parameter_definition_name` (undefined in its scope). "
        "Rename to `param` (the function argument) at lines 556 and 563."
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_minus_elements_does_not_crash_on_zero_inputs(js_src):
    """Node replay: minusElements must return cleanly when a param has no inputs.

    ThomasCZ's crash path (issue #880): heat_topology={"sources":[]} renders to
    "</br>" with no <input> elements; minusElements then references the undefined
    ``parameter_definition_name`` → ReferenceError → window.onload rejects.

    On master: exits 1 (ReferenceError).
    After fix (rename + early return): exits 0.
    """
    fn_src = _extract_function_src(js_src, "minusElements")

    node_script = (
        "const document = {\n"
        "  getElementById: (id) => {\n"
        "    if (id === 'heat_topology') {\n"
        "      return { getElementsByTagName: () => ({ length: 0 }) };\n"
        "    }\n"
        "    return null;\n"
        "  }\n"
        "};\n\n" + fn_src + "\n\ntry {\n"
        "  const r = minusElements('heat_topology');\n"
        "  process.stdout.write('OK result=' + r + '\\n');\n"
        "  process.exit(0);\n"
        "} catch (e) {\n"
        "  process.stderr.write('FAIL: ' + e.toString() + '\\n');\n"
        "  process.exit(1);\n"
        "}\n"
    )

    proc = _run_node(node_script)
    assert proc.returncode == 0, (
        f"minusElements threw on zero-input param (expected clean return):\n{proc.stderr.strip()}"
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_build_param_element_object_type_renders_input(_build_param_fns):
    """Node replay: buildParamElement for 'object' type must return HTML with <input>.

    ThomasCZ's config has heat_topology={"sources":[]}.  On master the
    nested-object render path produces the string "</br>" (no <input>), so
    minusElements subsequently finds length==0 and crashes (#880).

    On master: exits 1.
    After fix (render as JSON text box): exits 0.
    """
    check_fn, build_fn = _build_param_fns

    node_script = f"""\
{check_fn}
{build_fn}

const paramDef = {{
  input: "object",
  default_value: null,
  friendly_name: "Heat Topology",
  Description: "test"
}};
const config = {{ heat_topology: {{ sources: [] }} }};
const html = buildParamElement(paramDef, "heat_topology", config);

if (!html.includes('<input')) {{
  process.stderr.write('FAIL: no <input> in rendered HTML: ' + JSON.stringify(html) + '\\n');
  process.exit(1);
}}
process.exit(0);
"""
    proc = _run_node(node_script)
    assert proc.returncode == 0, (
        f"buildParamElement produced no <input> for object type with value={{sources:[]}}:\n"
        f"{proc.stderr.strip()}"
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_build_param_element_object_absent_config_no_null_string_value(_build_param_fns):
    """Node replay: object-type param absent from config must not render value="null".

    On master: placeholder=JSON.stringify(null)="null" → rendered as
    <input value=null>.  Saving that back stores the string "null" in
    config.json, causing a per-request backend warning (#904).

    On master: exits 1.
    After fix (placeholder="" for null defaults): exits 0.
    """
    check_fn, build_fn = _build_param_fns

    node_script = f"""\
{check_fn}
{build_fn}

const paramDef = {{
  input: "object",
  default_value: null,
  friendly_name: "Heat Topology",
  Description: "test"
}};
const config = {{}};  // heat_topology absent
const html = buildParamElement(paramDef, "heat_topology", config);

if (html.includes('value=null') || html.includes('value="null"')) {{
  process.stderr.write('FAIL: rendered string null as value attribute: ' + JSON.stringify(html) + '\\n');
  process.exit(1);
}}
process.exit(0);
"""
    proc = _run_node(node_script)
    assert proc.returncode == 0, (
        f"buildParamElement renders string 'null' as input value for absent config:\n"
        f"{proc.stderr.strip()}"
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_build_param_element_object_apostrophe_value_escaping(_build_param_fns):
    """Node replay: object-type render must escape double quotes so apostrophes in
    string values don't corrupt the HTML attribute.

    Single-quote attribute wrapping (value='...') breaks when the JSON contains
    an apostrophe, e.g. {"label":"John's panel"} → value='{"label":"John's panel"}'
    terminates the attribute at the apostrophe.

    On current (broken) code: exits 1 — no &quot; escape, single-quote wrap.
    After fix (double-quote wrap + replaceAll): exits 0.
    """
    check_fn, build_fn = _build_param_fns

    node_script = f"""\
{check_fn}
{build_fn}

const paramDef = {{
  input: "object",
  default_value: null,
  friendly_name: "Heat Topology",
  Description: "test"
}};
const config = {{ heat_topology: {{ label: "John's panel" }} }};
const html = buildParamElement(paramDef, "heat_topology", config);

// Must use &quot; escaping so double quotes inside the JSON are safe
if (!html.includes('&quot;')) {{
  process.stderr.write('FAIL: no &quot; escaping in rendered HTML: ' + JSON.stringify(html) + '\\n');
  process.exit(1);
}}
// Must NOT use single-quote attribute wrapping (apostrophes break it)
if (html.includes("value='")) {{
  process.stderr.write('FAIL: single-quote attribute wrapping detected: ' + JSON.stringify(html) + '\\n');
  process.exit(1);
}}
// Round-trip: decode &quot; → " then JSON.parse must recover the original object
const m = html.match(/value="([^"]*)"/);
if (!m) {{
  process.stderr.write('FAIL: no double-quoted value= attribute in: ' + JSON.stringify(html) + '\\n');
  process.exit(1);
}}
const decoded = m[1].replaceAll('&quot;', '"');
let parsed;
try {{ parsed = JSON.parse(decoded); }} catch (e) {{
  process.stderr.write('FAIL: value attr does not round-trip via JSON.parse: ' + e + '\\n');
  process.exit(1);
}}
if (parsed.label !== "John's panel") {{
  process.stderr.write('FAIL: round-trip value mismatch: ' + JSON.stringify(parsed) + '\\n');
  process.exit(1);
}}
process.exit(0);
"""
    proc = _run_node(node_script)
    assert proc.returncode == 0, (
        f"buildParamElement broke on apostrophe in object value:\n{proc.stderr.strip()}"
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_save_configuration_object_type_null_representations(js_src):
    """Node replay: saveConfiguration must store JSON null for '' and 'null' inputs.

    When heat_topology is absent from config, buildParamElement renders an empty
    text box (value="").  If saveConfiguration stores that as "" or as "null"
    (the string), the backend receives a non-null value and warns on every request.

    On original master (no object-type branch): exits 1 — param falls through to
    the generic path and config.heat_topology becomes "".
    After fix: exits 0 for both "" and "null" inputs.
    """
    save_fn_src = (
        _extract_const_src(js_src, "CAPACITY_SINGLETON_SAFE_PARAMS")
        + "\n"
        + _extract_function_src(js_src, "saveConfiguration")
    )

    # document must be a script-level var so saveConfiguration (outer scope) can see it.
    # We reassign it (without var/let) for each test case inside the IIFE.
    node_script = (
        "var capturedBody = null;\n"
        "var document = null;\n"
        "var fetch = async function(url, opts) {\n"
        "  capturedBody = opts.body;\n"
        "  return { status: 200, json: async function() { return {}; } };\n"
        "};\n"
        "function showChangeStatus() {}\n"
        "function errorAlert(msg) { throw new Error('errorAlert: ' + msg); }\n\n"
        + save_fn_src
        + "\n\n"
        "(async () => {\n"
        "  const paramDefs = {\n"
        "    General: {\n"
        "      heat_topology: { input: 'object', default_value: null,\n"
        "                       friendly_name: 'Heat Topology', Description: 'test' }\n"
        "    }\n"
        "  };\n"
        "  for (const [label, val] of [['empty string', ''], ['string null', 'null']]) {\n"
        "    capturedBody = null;\n"
        "    document = {\n"
        "      getElementsByClassName: function(cls) {\n"
        "        return cls === 'section-card' ? { length: 1 } : { length: 0 };\n"
        "      },\n"
        "      getElementById: function(id) {\n"
        "        if (id === 'config-box') return null;\n"
        "        if (id === 'heat_topology') return {\n"
        "          tagName: 'DIV',\n"
        "          getElementsByClassName: function(cls) {\n"
        "            return cls === 'param_input'\n"
        "              ? [{ type: 'text', value: val }]\n"
        "              : [];\n"
        "          }\n"
        "        };\n"
        "        return null;\n"
        "      }\n"
        "    };\n"
        "    await saveConfiguration(paramDefs);\n"
        "    const saved = JSON.parse(capturedBody);\n"
        "    if (saved.heat_topology !== null) {\n"
        "      process.stderr.write('FAIL (' + label + '): heat_topology should be null, got: '\n"
        "        + JSON.stringify(saved.heat_topology) + '\\n');\n"
        "      process.exit(1);\n"
        "    }\n"
        "  }\n"
        "  process.exit(0);\n"
        "})().catch(e => { process.stderr.write('FAIL: ' + e + '\\n'); process.exit(1); });\n"
    )

    proc = _run_node(node_script)
    assert proc.returncode == 0, (
        f"saveConfiguration did not store null for empty/null object input:\n{proc.stderr.strip()}"
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_build_param_element_object_html_special_chars_escaped(_build_param_fns):
    """Node replay: object-type render must escape &, <, > in addition to double quotes.

    JSON values can contain arbitrary strings, e.g. {"label":"<b>bold</b> & more"}.
    Unescaped & is interpreted as an HTML entity reference; < and > are malformed
    in attribute context.  All four must be escaped: &→&amp; <→&lt; >→&gt; "→&quot;.

    On current code: exits 1 — only " is escaped, &/</> are not.
    After fix: exits 0.
    """
    check_fn, build_fn = _build_param_fns

    node_script = f"""\
{check_fn}
{build_fn}

const paramDef = {{
  input: "object",
  default_value: null,
  friendly_name: "Heat Topology",
  Description: "test"
}};
const config = {{ heat_topology: {{ label: "<b>bold</b> & 'text'" }} }};
const html = buildParamElement(paramDef, "heat_topology", config);

const checks = [
  ['&amp;',  html.includes('&amp;'),  'unescaped & in attribute'],
  ['&lt;',   html.includes('&lt;'),   'unescaped < in attribute'],
  ['&gt;',   html.includes('&gt;'),   'unescaped > in attribute'],
];
for (const [entity, found, msg] of checks) {{
  if (!found) {{
    process.stderr.write('FAIL: ' + entity + ' missing — ' + msg + ': ' + JSON.stringify(html) + '\\n');
    process.exit(1);
  }}
}}
// Round-trip: HTML-decode then JSON.parse must recover original object
const m = html.match(/value="([^"]*)"/);
if (!m) {{
  process.stderr.write('FAIL: no double-quoted value= attribute in: ' + JSON.stringify(html) + '\\n');
  process.exit(1);
}}
const decoded = m[1]
  .replaceAll('&quot;', '"')
  .replaceAll('&amp;', '&')
  .replaceAll('&lt;', '<')
  .replaceAll('&gt;', '>');
let parsed;
try {{ parsed = JSON.parse(decoded); }} catch (e) {{
  process.stderr.write('FAIL: value attr does not round-trip via JSON.parse: ' + e + '\\n');
  process.exit(1);
}}
if (parsed.label !== "<b>bold</b> & 'text'") {{
  process.stderr.write('FAIL: round-trip mismatch: ' + JSON.stringify(parsed) + '\\n');
  process.exit(1);
}}
process.exit(0);
"""
    proc = _run_node(node_script)
    assert proc.returncode == 0, (
        f"buildParamElement did not escape HTML special chars in object value:\n{proc.stderr.strip()}"
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_save_configuration_object_type_invalid_json_shows_error(js_src):
    """Node replay: saveConfiguration must call errorAlert and not fetch when given invalid JSON.

    On current code: catch block stores raw string silently (fetch IS called,
    config gets garbage).
    After fix (errorAlert + return 0): fetch is NOT called, errorAlert is called.
    """
    save_fn_src = (
        _extract_const_src(js_src, "CAPACITY_SINGLETON_SAFE_PARAMS")
        + "\n"
        + _extract_function_src(js_src, "saveConfiguration")
    )

    node_script = (
        "var capturedBody = null;\n"
        "var errorAlertMsg = null;\n"
        "var document = null;\n"
        "var fetch = async function(url, opts) {\n"
        "  capturedBody = opts.body;\n"
        "  return { status: 200, json: async function() { return {}; } };\n"
        "};\n"
        "function showChangeStatus() {}\n"
        "function errorAlert(msg) { errorAlertMsg = msg; }\n\n" + save_fn_src + "\n\n"
        "(async () => {\n"
        "  const paramDefs = {\n"
        "    General: {\n"
        "      heat_topology: { input: 'object', default_value: null,\n"
        "                       friendly_name: 'Heat Topology', Description: 'test' }\n"
        "    }\n"
        "  };\n"
        "  document = {\n"
        "    getElementsByClassName: function(cls) {\n"
        "      return cls === 'section-card' ? { length: 1 } : { length: 0 };\n"
        "    },\n"
        "    getElementById: function(id) {\n"
        "      if (id === 'config-box') return null;\n"
        "      if (id === 'heat_topology') return {\n"
        "        tagName: 'DIV',\n"
        "        getElementsByClassName: function(cls) {\n"
        "          return cls === 'param_input'\n"
        "            ? [{ type: 'text', value: '{not valid json' }]\n"
        "            : [];\n"
        "        }\n"
        "      };\n"
        "      return null;\n"
        "    }\n"
        "  };\n"
        "  await saveConfiguration(paramDefs);\n"
        "  if (capturedBody !== null) {\n"
        "    process.stderr.write('FAIL: fetch was called despite invalid JSON; config stored: '\n"
        "      + capturedBody + '\\n');\n"
        "    process.exit(1);\n"
        "  }\n"
        "  if (!errorAlertMsg) {\n"
        "    process.stderr.write('FAIL: errorAlert was not called for invalid JSON\\n');\n"
        "    process.exit(1);\n"
        "  }\n"
        "  process.exit(0);\n"
        "})().catch(e => { process.stderr.write('FAIL: ' + e + '\\n'); process.exit(1); });\n"
    )

    proc = _run_node(node_script)
    assert proc.returncode == 0, (
        f"saveConfiguration silently stored invalid JSON instead of showing error:\n{proc.stderr.strip()}"
    )


# ---------------------------------------------------------------------------
# Issue #540 Part B, independent-review finding K1_LIST_VIEW_SCALAR_WRAP
# ---------------------------------------------------------------------------
#
# capacity_cost_per_kw / capacity_charge_interval_timesteps are declared
# array.float / array.int (to represent the K>1 list form, matching the
# existing multi-battery scalar-or-list precedent - see
# test_capacity_cost_per_kw_and_interval_timesteps_are_known_type_mismatches
# above). They live in the "Battery" param_definitions.json section, where
# the generic array +/- buttons are suppressed (see BATTERY_ARRAY_PARAMS and
# the "section != 'Battery'" guard in configuration_script.js) and are NOT
# in BATTERY_ARRAY_PARAMS themselves, so number_of_batteries never touches
# them either. buildParamElement's render path is VALUE-driven (branches on
# typeof value, not on the declared metadata type), so an existing scalar
# value still renders as exactly one <input>. But saveConfiguration's
# array/scalar decision (param_array) was METADATA-driven
# (`!input.search("array")`), independent of how many inputs were actually
# rendered - so a single rendered input for an array.*-typed field was
# always wrapped into a one-element array on Save, regardless of whether
# the loaded value was a bare scalar.
#
# capacity_cost_per_kw is a case where lengths carry real, differently-
# routed meaning: Optimization.__init__ picks the K=1 legacy code path
# purely from the TYPE of the value (scalar vs list/tuple), not its length
# (see optimization.py: `self._capacity_multi = isinstance(..., (list,
# tuple))`). So a routine list-view Save of an untouched legacy scalar
# config would silently reroute it onto the generic K=1-via-list path -
# mathematically equivalent, but not the byte-identical legacy
# construction Part B guarantees, and not something the user asked for.


def _run_save_list_view(js_src: str, param_defs: dict, rendered_inputs: dict) -> dict:
    """Node replay of saveConfiguration in list-view mode.

    `rendered_inputs` maps a param name to a list of currently-rendered
    input values (mirrors what buildParamElement would have already put in
    the DOM for that param's current config value) - this only exercises
    the SAVE-side collection logic, not rendering itself (already covered
    by the existing buildParamElement Node tests above).

    Returns the resulting config dict.
    """
    save_fn_src = (
        _extract_const_src(js_src, "CAPACITY_SINGLETON_SAFE_PARAMS")
        + "\n"
        + _extract_function_src(js_src, "saveConfiguration")
    )

    def _js_input(v):
        if isinstance(v, bool):
            return "{{ type: 'checkbox', checked: {} }}".format("true" if v else "false")
        return f"{{ type: 'number', value: '{v}' }}"

    elements_by_id = {}
    for name, values in rendered_inputs.items():
        js_values = ", ".join(_js_input(v) for v in values)
        elements_by_id[name] = (
            f"{{ tagName: 'DIV', getElementsByClassName: function(cls) "
            f"{{ return cls === 'param_input' ? [{js_values}] : []; }} }}"
        )

    get_by_id_cases = "\n".join(
        f"        if (id === '{name}') return {shim};" for name, shim in elements_by_id.items()
    )

    node_script = (
        "var capturedBody = null;\n"
        "var document = null;\n"
        "var fetch = async function(url, opts) {\n"
        "  capturedBody = opts.body;\n"
        "  return { status: 200, json: async function() { return {}; } };\n"
        "};\n"
        "function showChangeStatus() {}\n"
        "function errorAlert(msg) { throw new Error('errorAlert: ' + msg); }\n\n"
        + save_fn_src
        + "\n\n"
        "(async () => {\n"
        f"  const paramDefs = {json.dumps(param_defs)};\n"
        "  document = {\n"
        "    getElementsByClassName: function(cls) {\n"
        "      return cls === 'section-card' ? { length: 1 } : { length: 0 };\n"
        "    },\n"
        "    getElementById: function(id) {\n"
        "      if (id === 'config-box') return null;\n"
        f"{get_by_id_cases}\n"
        "      return null;\n"
        "    }\n"
        "  };\n"
        "  await saveConfiguration(paramDefs);\n"
        "  process.stdout.write(capturedBody === null ? 'null' : capturedBody);\n"
        "  process.exit(0);\n"
        "})().catch(e => { process.stderr.write('FAIL: ' + e + '\\n'); process.exit(1); });\n"
    )

    proc = _run_node(node_script)
    assert proc.returncode == 0, f"saveConfiguration (list view) crashed:\n{proc.stderr.strip()}"
    return json.loads(proc.stdout.strip())


_CAPACITY_PARAM_DEFS = {
    "Battery": {
        "capacity_cost_per_kw": {
            "input": "array.float",
            "default_value": 0.0,
            "friendly_name": "Capacity cost",
            "Description": "test",
        },
        "capacity_charge_interval_timesteps": {
            "input": "array.int",
            "default_value": 1,
            "friendly_name": "Capacity interval",
            "Description": "test",
        },
        "battery_nominal_energy_capacity": {
            "input": "array.int",
            "default_value": 5000,
            "friendly_name": "Battery capacity",
            "Description": "test",
        },
    }
}


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_k1_scalar_capacity_cost_survives_list_view_save():
    """Reproduces K1_LIST_VIEW_SCALAR_WRAP, then proves the fix: a legacy
    scalar capacity_cost_per_kw (one rendered input) must be saved back as
    a bare scalar, not wrapped into a one-element array.

    Before the fix: capacity_cost_per_kw saved as [3.0] (array-typed
    metadata unconditionally wins) - reroutes Optimization onto the
    generic K=1-via-list path instead of the untouched legacy scalar path.
    After the fix: capacity_cost_per_kw saved as 3.0 (scalar), because
    exactly one input was actually rendered for this specific field.
    """
    js_src = Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")
    config = _run_save_list_view(
        js_src,
        _CAPACITY_PARAM_DEFS,
        {
            "capacity_cost_per_kw": [3.0],
            "capacity_charge_interval_timesteps": [6],
            "battery_nominal_energy_capacity": [5000],
        },
    )
    assert config["capacity_cost_per_kw"] == 3.0, (
        f"legacy scalar capacity_cost_per_kw must survive a list-view Save unchanged, got "
        f"{config['capacity_cost_per_kw']!r}"
    )
    # JSON/JS has no int/float distinction (3.0 round-trips as the bare
    # number 3) - the meaningful assertion is "not a list", already covered
    # by the equality check above.
    assert not isinstance(config["capacity_cost_per_kw"], list)


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_k1_scalar_interval_timesteps_survives_list_view_save():
    """Same fix, second field: capacity_charge_interval_timesteps."""
    js_src = Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")
    config = _run_save_list_view(
        js_src,
        _CAPACITY_PARAM_DEFS,
        {
            "capacity_cost_per_kw": [3.0],
            "capacity_charge_interval_timesteps": [6],
            "battery_nominal_energy_capacity": [5000],
        },
    )
    assert config["capacity_charge_interval_timesteps"] == 6, (
        f"legacy scalar capacity_charge_interval_timesteps must survive a list-view Save "
        f"unchanged, got {config['capacity_charge_interval_timesteps']!r}"
    )
    assert isinstance(config["capacity_charge_interval_timesteps"], int)


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_k2_capacity_cost_list_survives_list_view_save():
    """An existing K=2 capacity_cost_per_kw (two rendered inputs) must still
    be saved back as a two-element array, unchanged."""
    js_src = Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")
    config = _run_save_list_view(
        js_src,
        _CAPACITY_PARAM_DEFS,
        {
            "capacity_cost_per_kw": [3.0, 7.0],
            "capacity_charge_interval_timesteps": [6],
            "battery_nominal_energy_capacity": [5000],
        },
    )
    assert config["capacity_cost_per_kw"] == [3.0, 7.0]


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_k2_cost_list_with_scalar_interval_broadcast_survives_list_view_save():
    """K=2 capacity_cost_per_kw alongside a SCALAR (broadcast-form)
    capacity_charge_interval_timesteps: the cost list must stay a list, and
    the interval value - independently rendered as a single input - must
    stay a scalar, not be forced into a one-element list just because its
    sibling field is K>1."""
    js_src = Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")
    config = _run_save_list_view(
        js_src,
        _CAPACITY_PARAM_DEFS,
        {
            "capacity_cost_per_kw": [3.0, 7.0],
            "capacity_charge_interval_timesteps": [6],
            "battery_nominal_energy_capacity": [5000],
        },
    )
    assert config["capacity_cost_per_kw"] == [3.0, 7.0]
    assert config["capacity_charge_interval_timesteps"] == 6
    assert isinstance(config["capacity_charge_interval_timesteps"], int)


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_k2_cost_and_interval_both_lists_survive_list_view_save():
    """K=2 with BOTH fields already lists (independent per-component
    measurement intervals) must round-trip unchanged."""
    js_src = Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")
    config = _run_save_list_view(
        js_src,
        _CAPACITY_PARAM_DEFS,
        {
            "capacity_cost_per_kw": [3.0, 7.0],
            "capacity_charge_interval_timesteps": [6, 3],
            "battery_nominal_energy_capacity": [5000],
        },
    )
    assert config["capacity_cost_per_kw"] == [3.0, 7.0]
    assert config["capacity_charge_interval_timesteps"] == [6, 3]


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_unrelated_battery_array_param_unaffected_by_capacity_fix():
    """The targeted fix must be scoped to exactly the two capacity
    structural params - an ordinary Battery-section array.* param
    (e.g. battery_nominal_energy_capacity) with a SINGLE rendered input
    (number_of_batteries == 1) must still be saved as a one-element array,
    matching existing multi-battery behaviour untouched."""
    js_src = Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")
    config = _run_save_list_view(
        js_src,
        _CAPACITY_PARAM_DEFS,
        {
            "capacity_cost_per_kw": [3.0],
            "capacity_charge_interval_timesteps": [6],
            "battery_nominal_energy_capacity": [5000],
        },
    )
    assert config["battery_nominal_energy_capacity"] == [5000], (
        "unrelated Battery array.* params must keep their existing metadata-driven "
        f"(always-array) save behaviour, got {config['battery_nominal_energy_capacity']!r}"
    )


@pytest.mark.skipif(_NODE is None, reason="node not in PATH")
def test_capacity_params_get_no_plus_minus_buttons_k2_creation_is_json_only():
    """Section 3: prove, don't assume, that the list-view genuinely cannot
    CREATE a K>1 capacity_cost_per_kw from a K=1 config - there is no +/-
    button for it at all, because the "Battery" section is unconditionally
    excluded from the generic array +/- button code (see
    `section != "Battery"` in buildParamContainers), the same exclusion
    that already applies to every per-battery array param. Creating (or
    changing) K therefore requires the JSON config box or a direct
    /set-config call - this is a documented, deliberate limitation, not an
    oversight (see docs/cookbook/tariff_demand_charge.md Step 5)."""
    js_src = Path("src/emhass/static/configuration_script.js").read_text(encoding="utf-8")
    fn_src = "\n".join(
        [
            _extract_const_src(js_src, "BATTERY_ARRAY_PARAMS"),
            _extract_function_src(js_src, "checkConfigParam"),
            _extract_function_src(js_src, "buildParamElement"),
            _extract_function_src(js_src, "buildParamContainers"),
        ]
    )

    node_script = f"""\
{fn_src}

const paramDef = {{
  capacity_cost_per_kw: {{
    input: "array.float", default_value: 0.0,
    friendly_name: "Capacity cost", Description: "test"
  }}
}};
const sectionBody = {{ innerHTML: "" }};
const sectionContainer = {{
  getElementsByClassName: (cls) => cls === "section-body" ? [sectionBody] : [],
  querySelectorAll: () => [],
}};
const document = {{ getElementById: (id) => id === "Battery" ? sectionContainer : null }};
buildParamContainers("Battery", paramDef, {{ capacity_cost_per_kw: 3.0 }}, []);
const hasButtons = sectionBody.innerHTML.includes("input-plus") || sectionBody.innerHTML.includes("input-minus");
process.stdout.write(hasButtons ? "HAS_BUTTONS" : "NO_BUTTONS");
process.exit(0);
"""
    proc = _run_node(node_script)
    assert proc.returncode == 0, f"buildParamContainers crashed:\n{proc.stderr.strip()}"
    assert proc.stdout.strip() == "NO_BUTTONS", (
        "capacity_cost_per_kw unexpectedly got +/- buttons in the Battery section - "
        "K2_CREATION_FROM_LIST_VIEW classification (JSON_ONLY_BY_DESIGN) is stale, "
        "update docs/cookbook/tariff_demand_charge.md Step 5"
    )
