# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 
"""
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import List, Optional

import aiohttp
import requests
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from loguru import logger
from pydantic import BaseModel

from chatpilot.apps.auth_utils import (
    get_current_user,
    get_verified_user,
    get_admin_user,
)
from chatpilot.chat_agent import ChatAgent
from chatpilot.config import (
    OPENAI_API_BASE_URLS,
    OPENAI_API_KEYS,
    CACHE_DIR,
    MODEL_FILTER_ENABLED,
    MODEL_FILTER_LIST,
    SERPER_API_KEY,
    OpenAIClientWrapper,
)
from chatpilot.constants import ERROR_MESSAGES

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.MODEL_FILTER_ENABLED = MODEL_FILTER_ENABLED
app.state.MODEL_FILTER_LIST = MODEL_FILTER_LIST

app.state.OPENAI_API_KEYS = OPENAI_API_KEYS
app.state.OPENAI_API_BASE_URLS = OPENAI_API_BASE_URLS
if app.state.OPENAI_API_KEYS and app.state.OPENAI_API_KEYS[0]:
    # chat agent
    app.state.AGENT_MODEL_NAME = "gpt-3.5-turbo"
    app.state.AGENT_SEARCH_ENGINE_NAME = "serper" if SERPER_API_KEY else "duckduckgo"
    app.state.AGENT_TEMPERATURE = 0.7
    app.state.AGENT_MAX_TOKENS = 1024
    app.state.AGENT_MAX_CONTEXT_TOKENS = 8192
    app.state.AGENT_STREAMING = True
    app.state.AGENT = ChatAgent(
        openai_model=app.state.AGENT_MODEL_NAME,
        search_engine_name=app.state.AGENT_SEARCH_ENGINE_NAME,
        verbose=True,
        temperature=app.state.AGENT_TEMPERATURE,
        max_tokens=app.state.AGENT_MAX_TOKENS,
        max_context_tokens=app.state.AGENT_MAX_CONTEXT_TOKENS,
        streaming=app.state.AGENT_STREAMING,
        openai_api_bases=OPENAI_API_BASE_URLS,
        openai_api_keys=OPENAI_API_KEYS,
        max_iterations=2,
        max_execution_time=60,
    )
    # openai audio speech (TTS)
    app.state.CLIENT_MANAGER = OpenAIClientWrapper(
        keys=OPENAI_API_KEYS, base_urls=OPENAI_API_BASE_URLS
    )


else:
    app.state.AGENT = None
    app.state.CLIENT_MANAGER = None

app.state.MODELS = {}


@app.middleware("http")
async def check_url(request: Request, call_next):
    if len(app.state.MODELS) == 0:
        await get_all_models()
    else:
        pass

    response = await call_next(request)
    return response


class UrlsUpdateForm(BaseModel):
    urls: List[str]


class KeysUpdateForm(BaseModel):
    keys: List[str]


@app.get("/urls")
async def get_openai_urls(user=Depends(get_admin_user)):
    return {"OPENAI_API_BASE_URLS": app.state.OPENAI_API_BASE_URLS}


@app.post("/urls/update")
async def update_openai_urls(form_data: UrlsUpdateForm, user=Depends(get_admin_user)):
    app.state.OPENAI_API_BASE_URLS = form_data.urls
    logger.info(f"update app.state.OPENAI_API_BASE_URLS: {app.state.OPENAI_API_BASE_URLS}")
    return {"OPENAI_API_BASE_URLS": app.state.OPENAI_API_BASE_URLS}


@app.get("/keys")
async def get_openai_keys(user=Depends(get_admin_user)):
    return {"OPENAI_API_KEYS": app.state.OPENAI_API_KEYS}


@app.post("/keys/update")
async def update_openai_key(form_data: KeysUpdateForm, user=Depends(get_admin_user)):
    app.state.OPENAI_API_KEYS = form_data.keys
    logger.info(f"update app.state.OPENAI_API_KEYS: {app.state.OPENAI_API_KEYS}")
    return {"OPENAI_API_KEYS": app.state.OPENAI_API_KEYS}


@app.post("/audio/speech")
async def speech(request: Request, user=Depends(get_verified_user)):
    r = None
    try:
        api_key, base_url = app.state.CLIENT_MANAGER.get_next_key_base_url()
        body = await request.body()
        name = hashlib.sha256(body).hexdigest()

        SPEECH_CACHE_DIR = Path(CACHE_DIR).joinpath("./audio/speech/")
        SPEECH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SPEECH_CACHE_DIR.joinpath(f"{name}.mp3")
        file_body_path = SPEECH_CACHE_DIR.joinpath(f"{name}.json")

        # Check if the file already exists in the cache
        if file_path.is_file():
            return FileResponse(file_path)

        headers = {}
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"

        try:
            r = requests.post(
                url=f"{base_url}/audio/speech",
                data=body,
                headers=headers,
                stream=True,
            )
            r.raise_for_status()

            # Save the streaming content to a file
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(file_body_path, "w") as f:
                json.dump(json.loads(body.decode("utf-8")), f)

            # Return the saved file
            return FileResponse(file_path)

        except Exception as e:
            logger.error(e)
            error_detail = "Server Connection Error"
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        error_detail = f"External: {res['error']}"
                except:
                    error_detail = f"External: {e}"

            raise HTTPException(status_code=r.status_code, detail=error_detail)

    except ValueError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.OPENAI_NOT_FOUND)


async def fetch_url(url, key):
    try:
        headers = {"Authorization": f"Bearer {key}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                return await response.json()
    except Exception as e:
        logger.error(f"Connection error: {e}")
        return None


def merge_models_lists(model_lists):
    merged_list = []

    for idx, models in enumerate(model_lists):
        merged_list.extend(
            [
                {**model, "urlIdx": idx}
                for model in models if model["id"]
            ]
        )

    return merged_list


async def get_all_models():
    if len(app.state.OPENAI_API_KEYS) == 1 and app.state.OPENAI_API_KEYS[0] == "":
        models = {"data": []}
    else:
        logger.debug(f"base urls size: {len(app.state.OPENAI_API_BASE_URLS)}, "
                     f"keys size: {len(app.state.OPENAI_API_KEYS)}")
        tasks = [
            fetch_url(f"{url}/models", app.state.OPENAI_API_KEYS[idx])
            for idx, url in enumerate(list(set(app.state.OPENAI_API_BASE_URLS)))
        ]
        responses = await asyncio.gather(*tasks)
        responses = list(
            filter(lambda x: x is not None and "error" not in x, responses)
        )
        models = {
            "data": merge_models_lists(
                list(map(lambda response: response["data"], responses))
            )
        }
        app.state.MODELS = {model["id"]: model for model in models["data"]}
        logger.debug(f"get_all_models done, size: {len(app.state.MODELS)}")
    return models


@app.get("/models")
@app.get("/models/{url_idx}")
async def get_models(url_idx: Optional[int] = None, user=Depends(get_current_user)):
    r = None
    if url_idx is None:
        models = await get_all_models()
        if app.state.MODEL_FILTER_ENABLED:
            if user.role == "user":
                models["data"] = list(
                    filter(
                        lambda model: model["id"] in app.state.MODEL_FILTER_LIST,
                        models["data"],
                    )
                )
                return models
        return models
    else:
        url = app.state.OPENAI_API_BASE_URLS[url_idx]
        try:
            r = requests.request(method="GET", url=f"{url}/models")
            r.raise_for_status()

            response_data = r.json()
            if url:
                response_data["data"] = list(
                    filter(lambda model: "gpt" in model["id"], response_data["data"])
                )

            return response_data
        except Exception as e:
            logger.error(e)
            error_detail = "Server Connection Error"
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        error_detail = f"External: {res['error']}"
                except:
                    error_detail = f"External: {e}"

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=error_detail,
            )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request, user=Depends(get_verified_user)):
    logger.debug(f"Proxying request to OpenAI: {path}")

    body = await request.body()
    body_dict = json.loads(body.decode("utf-8"))

    if not app.state.OPENAI_API_KEYS[0]:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.API_KEY_NOT_FOUND)

    try:
        # Update the agent when the setting changes
        openai_model_name = body_dict.get('model', 'gpt-3.5-turbo')
        max_tokens = body_dict.get("max_tokens", 1024)
        temperature = body_dict.get("temperature", 0.7)
        if (
                openai_model_name != app.state.AGENT_MODEL_NAME
                or max_tokens != app.state.AGENT_MAX_TOKENS
                or temperature != app.state.AGENT_TEMPERATURE
        ):
            app.state.AGENT.update_llm_params(
                model_name=openai_model_name,
                temperature=temperature,
                max_tokens=max_tokens
            )
            app.state.AGENT_MODEL_NAME = openai_model_name
            app.state.AGENT_MAX_TOKENS = max_tokens
            app.state.AGENT_TEMPERATURE = temperature

        num_ctx = body_dict.get('num_ctx', 8192)
        if num_ctx != app.state.AGENT_MAX_CONTEXT_TOKENS:
            app.state.AGENT_MAX_CONTEXT_TOKENS = num_ctx

        # Change api key for each request, to avoid rate limiting
        app.state.AGENT.update_credentials()

        messages = body_dict.get("messages", [])
        history = []
        for message in messages:
            if message["role"] == "user":
                history.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                history.append(AIMessage(content=message["content"]))
        history = history[:-1]  # drop the last message, which is the current user question
        user_question = ""
        if messages and messages[-1]["role"] == "user":
            user_question = messages[-1]["content"]

        events = await app.state.AGENT.astream_run(user_question, chat_history=history)
        created = int(time.time())

        async def event_generator():
            """组装为OpenAI格式流式输出"""
            async for event in events:
                kind = event['event']
                if kind in ['on_tool_start', 'on_chat_model_stream']:
                    if kind == "on_tool_start":
                        c = f"Invoking: `{event['name']}`\n```\n{event['data'].get('input', '')}\n```\n\n"
                    else:
                        c = event['data']['chunk'].content

                    data_structure = {
                        "id": event.get('id', 'default_id'),
                        "object": "chat.completion.chunk",
                        "created": event.get('created', created),
                        "model": openai_model_name,
                        "system_fingerprint": event.get('system_fingerprint', ''),
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": c},
                                "logprobs": None,
                                "finish_reason": None
                            }
                        ]
                    }
                    formatted_data = f"data: {json.dumps(data_structure)}\n\n"
                    yield formatted_data.encode()

            formatted_data_done = f"data: [DONE]\n\n"
            yield formatted_data_done.encode()

        return StreamingResponse(event_generator(), media_type='text/event-stream')
    except Exception as e:
        logger.error(e)
        error_detail = "Server Connection Error"
        raise HTTPException(status_code=500, detail=error_detail)