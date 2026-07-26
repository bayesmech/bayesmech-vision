from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def _write_progress(output_path: Path, **value: Any) -> None:
    path = output_path.with_name("progress.json")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def _tool_schema(tool: Any) -> dict[str, Any]:
    dumped = tool.model_dump(by_alias=True) if hasattr(tool, "model_dump") else {}
    return {
        "type": "function",
        "function": {
            "name": str(dumped.get("name") or getattr(tool, "name", "")),
            "description": str(
                dumped.get("description") or getattr(tool, "description", "") or ""
            ),
            "parameters": dumped.get("inputSchema")
            or dumped.get("input_schema")
            or getattr(tool, "inputSchema", None)
            or {"type": "object", "properties": {}},
        },
    }


def _normalized_call(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    function = item.get("function") if isinstance(item.get("function"), dict) else item
    name = str(function.get("name") or item.get("name") or "").strip()
    arguments = function.get("arguments", item.get("arguments", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not name or not isinstance(arguments, dict):
        return None
    return {"name": name, "arguments": arguments}


def _fallback_tool_calls(text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    pattern = r"<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>"
    for name, raw_arguments in re.findall(pattern, text, re.DOTALL):
        arguments: dict[str, Any] = {}
        for key, quoted, plain in re.findall(
            r"(\w+):(?:<\|\"\|>(.*?)<\|\"\|>|([^,}]*))", raw_arguments
        ):
            value = (quoted or plain).strip()
            try:
                arguments[key] = json.loads(value)
            except json.JSONDecodeError:
                arguments[key] = value.strip("'\"")
        calls.append({"name": name, "arguments": arguments})
    return calls


def _parsed_response(processor: Any, decoded: str) -> tuple[str, list[dict[str, Any]]]:
    parsed: Any = None
    if hasattr(processor, "parse_response"):
        try:
            parsed = processor.parse_response(decoded)
        except Exception:
            parsed = None
    content = ""
    calls: list[dict[str, Any]] = []
    if isinstance(parsed, dict):
        content = str(
            parsed.get("content") or parsed.get("text") or parsed.get("final") or ""
        ).strip()
        raw_calls = parsed.get("tool_calls") or parsed.get("tools") or []
        calls = [
            normalized
            for item in raw_calls
            if (normalized := _normalized_call(item)) is not None
        ]
    if not calls:
        calls = _fallback_tool_calls(decoded)
    if not content and not calls:
        content = re.sub(r"<\|[^>]+>", "", decoded).strip()
    return content, calls


def _result_data(result: Any) -> Any:
    data = getattr(result, "data", None)
    if data is not None:
        return data
    content = getattr(result, "content", None) or []
    texts = [str(item.text) for item in content if hasattr(item, "text")]
    return "\n".join(texts)


async def _run(request: dict[str, Any], output_path: Path) -> dict[str, Any]:
    _write_progress(
        output_path,
        stage="importing",
        message="Loading Gemma runtime libraries.",
        progress=0.05,
        current_step=1,
    )
    import torch
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    model_path = str(request["model_path"])
    token = str(request.get("runner_token") or "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = StreamableHttpTransport(
        str(request["runner_mcp_url"]),
        headers=headers,
        sse_read_timeout=120,
    )
    allowlist = set(request.get("tool_allowlist") or [])
    tool_trace: list[dict[str, Any]] = []

    torch.set_grad_enabled(False)
    _write_progress(
        output_path,
        stage="loading_processor",
        message="Loading the Gemma video processor.",
        progress=0.10,
        current_step=1,
    )
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    _write_progress(
        output_path,
        stage="loading_model",
        message="Loading Gemma 4 into GPU memory.",
        progress=0.20,
        current_step=2,
    )
    model = AutoModelForMultimodalLM.from_pretrained(
        model_path,
        dtype=torch.bfloat16,
        device_map="auto",
        low_cpu_mem_usage=True,
        local_files_only=True,
    )
    model.eval()
    _write_progress(
        output_path,
        stage="preparing_video",
        message="Preparing sampled video frames and available tools.",
        progress=0.45,
        current_step=2,
    )

    history = [
        {
            "role": "assistant" if item.get("role") == "assistant" else "user",
            "content": str(item.get("text") or ""),
        }
        for item in request.get("history") or []
        if str(item.get("text") or "").strip()
    ][-12:]
    frame_paths = [str(path) for path in request.get("frame_paths") or []]
    timestamps = list(request.get("timestamps_sec") or [])
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "These are timestamped frames sampled in temporal order from the "
                "active video recording. Use them as one video sequence."
            ),
        }
    ]
    for index, frame_path in enumerate(frame_paths):
        timestamp = float(timestamps[index]) if index < len(timestamps) else index
        content.append({"type": "text", "text": f"Frame at {timestamp:.3f} seconds:"})
        content.append({"type": "image", "url": frame_path})
    content.append({"type": "text", "text": str(request["message"])})
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are the BayesMech video analysis assistant. Ground answers in "
                "the supplied recording frames. Use available tools when they are "
                "needed, never invent tool results, and be concise."
            ),
        },
        *history,
        {"role": "user", "content": content},
    ]

    async with Client(transport, timeout=120) as client:
        discovered = await client.list_tools()
        available = {
            str(getattr(tool, "name", "")): tool for tool in discovered if tool
        }
        selected = [available[name] for name in sorted(allowlist) if name in available]
        tools = [_tool_schema(tool) for tool in selected]
        max_turns = max(1, min(int(request.get("max_tool_turns") or 6), 12))
        final_text = ""

        for _turn in range(max_turns):
            _write_progress(
                output_path,
                stage="generating",
                message=(
                    "Gemma is generating a response."
                    if _turn == 0
                    else "Gemma is reasoning over tool results."
                ),
                progress=min(0.90, 0.55 + (0.08 * _turn)),
                current_step=3,
            )
            inputs = processor.apply_chat_template(
                messages,
                tools=tools,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
                add_generation_prompt=True,
                enable_thinking=False,
            ).to(model.device)
            input_length = int(inputs["input_ids"].shape[-1])
            output = model.generate(
                **inputs,
                max_new_tokens=max(
                    64, min(int(request.get("max_new_tokens") or 768), 2048)
                ),
                do_sample=False,
                use_cache=True,
            )
            decoded = processor.decode(
                output[0][input_length:], skip_special_tokens=False
            )
            text, calls = _parsed_response(processor, decoded)
            if not calls:
                final_text = text or decoded.strip()
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": text or "I will use the available tools.",
                }
            )
            results = []
            for call in calls:
                name = call["name"]
                if name not in allowlist or name not in available:
                    result_data: Any = {
                        "error": f"Tool {name!r} is not allowed for autonomous use."
                    }
                else:
                    try:
                        result = await client.call_tool(name, call["arguments"])
                        result_data = _result_data(result)
                    except Exception as exc:
                        result_data = {"error": str(exc)}
                trace = {
                    "name": name,
                    "arguments": call["arguments"],
                    "result": result_data,
                }
                tool_trace.append(trace)
                results.append(trace)
            _write_progress(
                output_path,
                stage="calling_tools",
                message=f"Ran {len(results)} tool call(s); returning results to Gemma.",
                progress=min(0.94, 0.68 + (0.08 * _turn)),
                current_step=3,
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Tool execution results follow. Use them to answer the "
                        f"original request:\n{json.dumps(results, default=str)}"
                    ),
                }
            )
        else:
            final_text = "The tool-call limit was reached before a final answer."

    return {
        "text": final_text,
        "model": str(request.get("model_id") or model_path),
        "tool_calls": tool_trace,
        "sampled_frame_count": len(frame_paths),
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: worker.py REQUEST_JSON OUTPUT_JSON")
    request_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = asyncio.run(_run(request, output_path))
    output_path.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
