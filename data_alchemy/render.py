"""Render synthesized tables to files (CSV/JSON/Markdown) and bundle them into a zip."""

import csv as csvmod
import io
import json
import tempfile
import zipfile
from pathlib import Path

EXTENSIONS = {"CSV": "csv", "JSON": "json", "Markdown": "md"}


def _format_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _to_csv(columns, rows):
    buffer = io.StringIO()
    writer = csvmod.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_format_cell(row.get(c)) for c in columns])
    return buffer.getvalue()


def _to_markdown(columns, rows):
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        cells = [_format_cell(row.get(c)).replace("|", "\\|") for c in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_files(rendered, dataset_format):
    """Serialize each table to a file dict {"filename", "content"} in the chosen format."""
    extension = EXTENSIONS.get(dataset_format, "csv")
    files = []
    for name, table in rendered.items():
        columns, rows = table["columns"], table["rows"]
        if dataset_format == "JSON":
            content = json.dumps(rows, indent=2, default=str)
        elif dataset_format == "Markdown":
            content = _to_markdown(columns, rows)
        else:
            content = _to_csv(columns, rows)
        files.append({"filename": f"{name}.{extension}", "content": content})
    return files


def make_zip(files):
    """Write the rendered files into a temp zip and return its path."""
    output_dir = Path(tempfile.mkdtemp(prefix="dataalchemy_"))
    zip_path = output_dir / "dataalchemy_dataset.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, file_info in enumerate(files or [], start=1):
            filename = Path(
                str(file_info.get("filename") or f"dataset_{index}.txt")
            ).name
            content = file_info.get("content", "")
            archive.writestr(filename, str(content))
    return str(zip_path)
