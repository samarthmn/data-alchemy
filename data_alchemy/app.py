"""DataAlchemy: the conversation flow and the Gradio chat app.

The running application lives here as plain functions. Pieces worth keeping separate
live in sibling modules:
    data_alchemy.models     — Ollama discovery + chat client (OllamaModels / load_models)
    data_alchemy.prompts    — system prompts
    data_alchemy.llm        — JSON chat calls + requirement extraction
    data_alchemy.synthesis  — turning a spec into rows
    data_alchemy.render     — serializing rows to files + zip
    data_alchemy.config     — shared option sets and limits
"""

import json
import threading
from functools import partial

import gradio as gr
from dotenv import load_dotenv

from data_alchemy.config import (
    COMPLEXITY_OPTIONS,
    FORMAT_OPTIONS,
    REQUIRED_FIELDS,
    ROW_OPTIONS,
)
from data_alchemy.llm import (
    call_llm_json,
    explicit_complexity,
    explicit_format,
    explicit_rows,
    required_file_count,
)
from data_alchemy.models import OllamaModels
from data_alchemy.prompts import CHECKER_MESSAGE, SYSTEM_MESSAGE
from data_alchemy.render import make_zip, render_files
from data_alchemy.synthesis import synthesize_tables

load_dotenv(override=True)


# --------------------------------------------------------------------------- #
# Option cards and thinking indicator
# --------------------------------------------------------------------------- #


def next_question_card(state):
    """Return the option card for the next missing field, or None if complete."""
    if not state.get("complexity"):
        return gr.ChatMessage(
            role="assistant",
            content="How complex should the dataset be?",
            options=[
                {"label": option, "value": f"complexity:{option}"}
                for option in COMPLEXITY_OPTIONS
            ],
        )
    if not state.get("format"):
        return gr.ChatMessage(
            role="assistant",
            content="Which format would you like?",
            options=[
                {"label": option, "value": f"format:{option}"}
                for option in FORMAT_OPTIONS
            ],
        )
    if not state.get("rows"):
        return gr.ChatMessage(
            role="assistant",
            content="How many rows (or items) in total? Pick one or type a number.",
            options=[
                {"label": option, "value": f"rows:{option}"} for option in ROW_OPTIONS
            ],
        )
    return None


def _spinner_message(caption):
    """A 'thinking' bubble with a live spinner and a single static caption."""
    return gr.ChatMessage(
        role="assistant",
        content="",
        metadata={"title": caption, "status": "pending"},
    )


def stream_thinking(chat, holder, work_fn, caption):
    """Run work_fn() in a background thread while a thinking bubble spins.

    Shows a single spinner (it animates client-side) until the work finishes.
    The outcome is stored in holder as {"result": ...} or {"error": ...}.
    """
    holder.clear()

    def runner():
        try:
            holder["result"] = work_fn()
        except Exception as exc:  # surfaced to the caller via holder
            holder["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()

    chat.append(_spinner_message(caption))
    yield chat
    thread.join()
    chat.pop()


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def build_generation_message(client, state, model):
    """Ask the model for a dataset spec, synthesize rows, and return a ChatMessage."""
    messages = [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "topic": state["topic"],
                    "complexity": state["complexity"],
                    "format": state["format"],
                    "row_count": state["rows"],
                    "minimum_tables": required_file_count(state["complexity"]),
                    "table_rule": "Return a spec only. Medium needs at least two related tables. Hard needs at least three related tables.",
                    "row_rule": "Per-table 'rows' values should sum to approximately row_count.",
                }
            ),
        },
    ]
    try:
        result = call_llm_json(client, messages, model)
    except Exception as exc:
        return gr.ChatMessage(
            role="assistant",
            content=f"I couldn't parse the generator response. Try again. ({exc})",
        )

    if result.get("status") != "success":
        message = (
            result.get("error", {}).get("message")
            or result.get("reply")
            or "Generation failed. Try again."
        )
        return gr.ChatMessage(role="assistant", content=message)

    data = result.get("data") or {}
    tables = data.get("tables") or []
    minimum_tables = required_file_count(state.get("complexity"))
    if len(tables) < minimum_tables:
        return gr.ChatMessage(
            role="assistant",
            content=(
                f"The generator designed {len(tables)} table(s), but {state.get('complexity')} "
                f"datasets require at least {minimum_tables} related tables. Please try again."
            ),
        )

    try:
        rendered = synthesize_tables(tables)
        files = render_files(rendered, state["format"])
    except Exception as exc:
        return gr.ChatMessage(
            role="assistant",
            content=f"I couldn't synthesize the dataset from the spec. Try again. ({exc})",
        )

    if not files:
        return gr.ChatMessage(
            role="assistant",
            content="The generator did not design any tables. Try again.",
        )

    total_rows = sum(len(table["rows"]) for table in rendered.values())
    zip_path = make_zip(files)
    reply = result.get("reply") or "Generated the dataset."
    description = data.get("description") or "Dataset files are ready."
    return gr.ChatMessage(
        role="assistant",
        content=[
            f"{reply}\n\n{description}\n\n**{total_rows:,} rows** across "
            f"{len(files)} table file(s).",
            gr.File(value=zip_path, label="Download dataset"),
        ],
    )


def stream_generation(client, chat, state, model):
    """Show a 'generating' spinner, then append the generated result."""
    holder = {}
    for snapshot in stream_thinking(
        chat,
        holder,
        lambda: build_generation_message(client, state, model),
        "Generating your dataset…",
    ):
        yield snapshot
    if "error" in holder:
        chat.append(
            gr.ChatMessage(
                role="assistant",
                content=f"I couldn't generate the dataset. Try again. ({holder['error']})",
            )
        )
    else:
        chat.append(holder["result"])
    yield chat


# --------------------------------------------------------------------------- #
# Conversation handlers
# --------------------------------------------------------------------------- #


def clear_card_options(chat, field):
    """Remove the clickable buttons from an already-answered question card."""
    prefix = f"{field}:"
    for msg in chat:
        options = (
            msg.get("options")
            if isinstance(msg, dict)
            else getattr(msg, "options", None)
        )
        if not options:
            continue
        values = [
            (opt.get("value") if isinstance(opt, dict) else getattr(opt, "value", ""))
            or ""
            for opt in options
        ]
        if any(str(value).startswith(prefix) for value in values):
            if isinstance(msg, dict):
                msg["options"] = None
            else:
                msg.options = None


def handle_user_message(client, message, chat, state, model):
    chat = list(chat or [])
    state = {**{field: None for field in REQUIRED_FIELDS}, **(state or {})}
    message = (message or "").strip()
    if not message:
        yield chat, "", state
        return

    # Echo the user message and clear the textbox immediately.
    chat.append(gr.ChatMessage(role="user", content=message))
    yield chat, "", state

    # Initial loading: show a thinking spinner while the checker runs.
    checker_payload = {"current_state": state, "user_message": message}
    holder = {}
    for snapshot in stream_thinking(
        chat,
        holder,
        lambda: call_llm_json(
            client,
            [
                {"role": "system", "content": CHECKER_MESSAGE},
                {"role": "user", "content": json.dumps(checker_payload)},
            ],
            model,
        ),
        "Thinking…",
    ):
        yield snapshot, "", state
    if "error" in holder:
        chat.append(
            gr.ChatMessage(
                role="assistant",
                content=f"I couldn't understand that request. Please include a topic, complexity, format, and number of rows. ({holder['error']})",
            )
        )
        yield chat, "", state
        return
    result = holder["result"]

    topic = str(result.get("topic") or "").strip()
    if topic and topic.lower() not in {"null", "none"}:
        state["topic"] = topic
    complexity = explicit_complexity(message)
    if complexity:
        state["complexity"] = complexity
    dataset_format = explicit_format(message)
    if dataset_format:
        state["format"] = dataset_format

    rows_value = result.get("rows")
    if isinstance(rows_value, str):
        rows_value = rows_value.strip()
        rows_value = int(rows_value) if rows_value.isdigit() else None
    if isinstance(rows_value, int) and rows_value > 0:
        state["rows"] = rows_value
    rows_explicit = explicit_rows(message)
    if rows_explicit:
        state["rows"] = rows_explicit

    if not state.get("topic"):
        reply = result.get("reply") or "What dataset topic should I generate?"
        chat.append(gr.ChatMessage(role="assistant", content=reply))
        yield chat, "", state
        return

    # Ask one question at a time, step by step.
    card = next_question_card(state)
    if card:
        chat.append(card)
        yield chat, "", state
        return

    # Everything collected in one go -> generate with a live indicator.
    for snapshot in stream_generation(client, chat, state, model):
        yield snapshot, "", state


def capture_choice(evt: gr.SelectData):
    """Stash the clicked option's value so the chained handlers can read it.

    gr.SelectData is only delivered to the function bound directly to the
    event, not to functions chained with .then(), so we capture it here.
    """
    return str(evt.value or "")


def handle_card_choice(client, chat, state, model, choice):
    chat = list(chat or [])
    state = {**{field: None for field in REQUIRED_FIELDS}, **(state or {})}
    choice = str(choice or "")
    if ":" not in choice:
        yield chat, state
        return

    field, value = choice.split(":", 1)
    if field == "complexity" and value in COMPLEXITY_OPTIONS:
        state["complexity"] = value
    elif field == "format" and value in FORMAT_OPTIONS:
        state["format"] = value
    elif field == "rows" and value in ROW_OPTIONS:
        state["rows"] = int(value)
    else:
        yield chat, state
        return

    # Retire the answered card's buttons and echo the choice as a user message.
    clear_card_options(chat, field)
    chat.append(gr.ChatMessage(role="user", content=value))
    yield chat, state

    # Advance to the next missing question, if any.
    card = next_question_card(state)
    if card:
        chat.append(card)
        yield chat, state
        return

    if all(state.get(f) for f in REQUIRED_FIELDS):
        for snapshot in stream_generation(client, chat, state, model):
            yield snapshot, state


# --------------------------------------------------------------------------- #
# Gradio app
# --------------------------------------------------------------------------- #


def lock_controls():
    """Disable input + buttons while the app is thinking or generating."""
    return tuple(gr.update(interactive=False) for _ in range(4))


def unlock_controls():
    """Re-enable input + buttons once work has finished."""
    return tuple(gr.update(interactive=True) for _ in range(4))


def build_demo(models: OllamaModels) -> gr.Blocks:
    """Build the Gradio app wired to the handlers with ``models`` bound in."""
    handle_message = partial(handle_user_message, models.client)
    handle_choice = partial(handle_card_choice, models.client)

    with gr.Blocks(title="DataAlchemy") as demo:
        gr.Markdown("# DataAlchemy")
        gr.Markdown("Generate a dataset based on the topic, complexity and format.")

        model_dropdown = gr.Dropdown(
            choices=models.names,
            value=models.default,
            label="Local model",
            info="Choose a local Ollama model to chat with.",
        )

        chatbot = gr.Chatbot(
            allow_file_downloads=True,
            height=520,
        )
        collected_state = gr.State({field: None for field in REQUIRED_FIELDS})
        pending_choice = gr.State("")

        with gr.Row():
            user_input = gr.Textbox(
                placeholder="Describe the dataset you want, e.g. retail orders, hard, CSV, 100 rows",
                show_label=False,
                scale=8,
            )
            send_button = gr.Button("Send", variant="primary", scale=1)
            clear_button = gr.Button("Clear", scale=1)

        controls = [model_dropdown, user_input, send_button, clear_button]

        send_button.click(lock_controls, outputs=controls).then(
            handle_message,
            inputs=[user_input, chatbot, collected_state, model_dropdown],
            outputs=[chatbot, user_input, collected_state],
        ).then(unlock_controls, outputs=controls)

        user_input.submit(lock_controls, outputs=controls).then(
            handle_message,
            inputs=[user_input, chatbot, collected_state, model_dropdown],
            outputs=[chatbot, user_input, collected_state],
        ).then(unlock_controls, outputs=controls)

        # Capture the SelectData first (only the direct handler receives it), then
        # lock -> handle -> unlock using the stashed choice value.
        chatbot.option_select(capture_choice, outputs=pending_choice).then(
            lock_controls, outputs=controls
        ).then(
            handle_choice,
            inputs=[chatbot, collected_state, model_dropdown, pending_choice],
            outputs=[chatbot, collected_state],
        ).then(unlock_controls, outputs=controls)

        clear_button.click(
            lambda: ([], "", {field: None for field in REQUIRED_FIELDS}),
            outputs=[chatbot, user_input, collected_state],
        )

    return demo
