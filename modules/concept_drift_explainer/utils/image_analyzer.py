"""Vision-LLM wrapper for describing standalone images.

Takes a pre-built LangChain ``BaseChatModel`` so the model is chosen once at
the top of the pipeline (Module settings → AI models) and re-used here. Works
with any vision-capable OpenAI chat model (e.g. GPT-4o / GPT-4o-mini) that
supports the ``image_url`` content block.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage


def encode_image(image_path: Path) -> str:
    try:
        return base64.b64encode(image_path.read_bytes()).decode("utf-8")
    except Exception as e:
        logging.error("Error encoding image %s: %s", image_path, e)
        return ""


def analyze_image_content(image_path: Path, *, llm: BaseChatModel) -> str:
    logging.info("Analyzing image content for: %s", image_path.name)

    base64_image = encode_image(image_path)
    if not base64_image:
        return "Error encoding image."

    mime_type = f"image/{image_path.suffix.lstrip('.').lower() or 'png'}"

    prompt = [
        HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "You are an expert business analyst. Analyze the "
                        "following image and provide a detailed, factual "
                        "description of its content.\n"
                        "- If it is a chart or graph, describe what it shows, "
                        "including its title, axes, and the data trends.\n"
                        "- If it is an organizational chart, describe the "
                        "reporting structure, roles, and departments shown.\n"
                        "- If it contains text, transcribe the text accurately.\n"
                        "Your description will be used as context to explain "
                        "a business process change, so focus on information "
                        "relevant to that goal."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{base64_image}"},
                },
            ]
        )
    ]

    try:
        response = llm.invoke(prompt)
        return response.content
    except Exception as e:
        logging.error("Error analyzing image with LLM: %s", e)
        return f"Error analyzing image: {e}"
