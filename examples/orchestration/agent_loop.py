"""
The agent loop: screenshot + spoken instruction -> tool calls -> acted-on desktop.

Excerpt from the voice client (`aihylu.py`), trimmed for publication.

This is the "agent" hotkey mode. The user holds Ctrl+Shift+A, says what they
want, and the model drives the machine: it sees the screen, clicks, types,
scrolls, opens URLs, and takes a fresh screenshot to check its own work.

Design notes worth stealing:

* **Vision in, vision back.** `take_screenshot` returns an image *inside* the
  tool_result, so the model re-grounds itself after every action instead of
  reasoning blind off its first glance.
* **An explicit `finish` tool.** Relying on `end_turn` alone leaves the loop
  guessing whether the model is done or just thinking out loud.
* **A hard step ceiling.** An agent with a mouse and no budget is a liability.
* **Every tool call is streamed to the UI** before and after it runs, so a human
  can watch what is about to happen and kill it.
"""

import time

import anthropic

MAX_STEPS = 20

AGENT_TOOLS = [
    {
        "name": "take_screenshot",
        "description": "Capture the screen again to verify the result of an action.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "Click at absolute screen coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into the focused field.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "press_keys",
        "description": "Press a key chord, e.g. ['cmd', 'l'].",
        "input_schema": {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            "required": ["keys"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll at coordinates by dy lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "dy": {"type": "integer"},
            },
            "required": ["x", "y", "dy"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a URL in the default browser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "finish",
        "description": "The task is complete. Report what was done.",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
]


def run_agent(instruction, screenshot_b64, *, model, system_prompt, ui, api_key):
    """Drive the desktop until the model calls `finish` or the budget runs out."""
    client = anthropic.Anthropic(api_key=api_key)

    content = []
    if screenshot_b64:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshot_b64,
                },
            }
        )
    content.append({"type": "text", "text": instruction})

    messages = [{"role": "user", "content": content}]
    final_text = ""

    for step in range(MAX_STEPS):
        ui("log", f"step {step + 1}/{MAX_STEPS}")

        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            tools=AGENT_TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        for block in resp.content:
            if getattr(block, "text", None):
                final_text += block.text
                ui("chunk", block.text)

        if resp.stop_reason != "tool_use":
            break  # end_turn, max_tokens, or a refusal — either way, stop

        tool_results, finished = [], False

        for block in resp.content:
            if block.type != "tool_use":
                continue

            result, kind = execute_tool(block.name, block.input, ui)

            if kind == "finish":
                final_text, finished = result, True
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
                break

            if kind == "image/png":
                # Feed the new screenshot straight back as vision input.
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": result,
                                },
                            }
                        ],
                    }
                )
            else:
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

        if finished:
            break

        messages.append({"role": "user", "content": tool_results})

    return final_text or "Done."


def execute_tool(name, args, ui):
    """Dispatch one tool call. Returns (result, kind)."""
    ui("agent_step", {"tool": name, "args": args, "status": "running"})

    if name == "take_screenshot":
        shot = take_screenshot()
        ui("agent_step", {"tool": name, "status": "done", "result": "captured"})
        return shot, "image/png"

    if name == "click":
        x, y = args["x"], args["y"]
        click_at(x, y)
        time.sleep(0.3)  # let the UI settle before the next screenshot
        ui("agent_step", {"tool": name, "status": "done", "result": f"click ({x}, {y})"})
        return f"Clicked ({x}, {y})", "text"

    if name == "type_text":
        paste_to_active_field(args["text"])
        time.sleep(0.2)
        ui("agent_step", {"tool": name, "status": "done"})
        return f"Typed: {args['text'][:80]}", "text"

    if name == "press_keys":
        press_keys(args["keys"])
        time.sleep(0.3)
        ui("agent_step", {"tool": name, "status": "done"})
        return f"Pressed: {args['keys']}", "text"

    if name == "scroll":
        scroll_at(args["x"], args["y"], args["dy"])
        time.sleep(0.2)
        ui("agent_step", {"tool": name, "status": "done"})
        return f"Scrolled dy={args['dy']}", "text"

    if name == "open_url":
        open_url(args["url"])
        time.sleep(1.0)  # page load
        ui("agent_step", {"tool": name, "status": "done"})
        return f"Opened {args['url']}", "text"

    if name == "finish":
        message = args.get("message", "Done.")
        ui("agent_step", {"tool": name, "status": "done", "result": message})
        return message, "finish"

    return f"Unknown tool: {name}", "text"


# ── Host bindings (Quartz / pyobjc in the real client) ───────────────────────
def take_screenshot(): ...
def click_at(x, y): ...
def paste_to_active_field(text): ...
def press_keys(keys): ...
def scroll_at(x, y, dy): ...
def open_url(url): ...
