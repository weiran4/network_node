import contextlib
import io
import json

import blackbox_validation_api
import optimized_elimination_api
import reduce_api


def _dump_with(module, message: str) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module._dump_json({"ok": False, "error": message})
    return buffer.getvalue()


def test_cli_json_error_output_is_ascii_safe_for_windows_code_pages():
    message = "内部消元块 Gkk 对节点 N2, N1 是奇异的"

    for module in (reduce_api, optimized_elimination_api, blackbox_validation_api):
        text = _dump_with(module, message)
        text.encode("ascii")
        assert "\\u" in text
        assert json.loads(text)["error"] == message
