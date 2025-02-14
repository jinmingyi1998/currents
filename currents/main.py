#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Copyright (c) 2024 Kunlunxin.com, Inc. All Rights Reserved

Authors:     Mingyi Jin(jinmingyi@baidu.com)
Date:        Jul 08, 2024, 15:46:25+08:00

Description: currents proxy server entrypoint
"""

import argparse
import asyncio
import copy
import time
from collections import defaultdict, deque

import aiohttp
import tabulate
from aiohttp import web
from yarl import URL


class RequestQueue:
    """Request Queue
    maintain recent requests.
    """
    STATUS_KEYS = ["total_tokens", "prompt_tokens", "completion_tokens", "count", "elapsed_time_s"]
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(RequestQueue, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.q30s = deque()
        self.q1min = deque()
        self.q3min = deque()

        # model_id : status_dict
        self.usage_info_30s = defaultdict(
            lambda: {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "count": 0,
                "elapsed_time_s": 0,
            }
        )
        self.usage_info_1min = defaultdict(
            lambda: {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "count": 0,
                "elapsed_time_s": 0,
            }
        )
        self.usage_info_3min = defaultdict(
            lambda: {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "count": 0,
                "elapsed_time_s": 0,
            }
        )
        self.lock = asyncio.Lock()

    async def appendleft(self, usage):
        """appendleft the queue"""
        async with self.lock:
            print(usage)
            self.q30s.appendleft(usage)
            model_name = usage["model"]
            for k in self.STATUS_KEYS:
                self.usage_info_30s[model_name][k] += usage[k]

    async def update(self):
        """update the queue"""
        async with self.lock:
            now = time.time()
            while self.q3min:
                t = self.q3min.pop()
                if now - t["time"] < 180:
                    self.q3min.append(t)
                    break
                else:
                    model_name = t["model"]
                    for k in self.STATUS_KEYS:
                        self.usage_info_3min[model_name][k] -= t[k]
            while self.q1min:
                t = self.q1min.pop()
                if now - t["time"] < 60:
                    self.q1min.append(t)
                    break
                else:
                    self.q3min.appendleft(t)
                    model_name = t["model"]
                    for k in self.STATUS_KEYS:
                        self.usage_info_1min[model_name][k] -= t[k]
                        self.usage_info_3min[model_name][k] += t[k]
            while self.q30s:
                t = self.q30s.pop()
                if now - t["time"] < 30:
                    self.q30s.append(t)
                    break
                else:
                    self.q1min.appendleft(t)
                    model_name = t["model"]
                    for k in self.STATUS_KEYS:
                        self.usage_info_30s[model_name][k] -= t[k]
                        self.usage_info_1min[model_name][k] += t[k]

    def __len__(self):
        return len(self.q30s) + len(self.q1min) + len(self.q3min)


def parse_args():
    """parse arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--llm-host", type=str, required=True)
    parser.add_argument("--llm-port", type=int, default=8000)
    args = parser.parse_args()
    return args


async def show_status():
    """show status"""
    rq = RequestQueue()
    if len(rq) == 0:
        return
    await rq.update()
    TABLE_HEADERS = [
        "Model",
        "Total Tokens",
        "Prompt Tokens",
        "Completion Tokens",
        "Count",
        "Avg Latency(s)",
        "Avg Throughput/request(Tokens/s)",
        "qps",
    ]

    def show_table(data_dict, duration):
        print(f"last {duration} seconds:")
        data_list = [
            [
                model_name,
                v["total_tokens"],
                v["prompt_tokens"],
                v["completion_tokens"],
                v["count"],
                v["elapsed_time_s"] / v["count"] if v["count"] > 0 else 0,
                v["completion_tokens"] / duration,
                v["count"] / duration,
            ]
            for model_name, v in data_dict.items()
        ]
        table = tabulate.tabulate(
            data_list,
            headers=TABLE_HEADERS,
            tablefmt="simple_grid",
        )
        print(table)

    data_dict_1min = copy.deepcopy(rq.usage_info_30s)
    for model_name in rq.usage_info_1min.keys():
        for k in RequestQueue.STATUS_KEYS:
            data_dict_1min[model_name][k] += rq.usage_info_1min[model_name][k]

    data_dict_3min = copy.deepcopy(data_dict_1min)
    for model_name in rq.usage_info_3min.keys():
        for k in RequestQueue.STATUS_KEYS:
            data_dict_3min[model_name][k] += rq.usage_info_3min[model_name][k]

    show_table(rq.usage_info_30s, 30)
    show_table(data_dict_1min, 60)
    show_table(data_dict_3min, 180)


async def show_status_loop():
    """show status loop every 5 seconds"""
    while True:
        await show_status()
        await asyncio.sleep(5)


async def start_background_tasks(app):
    """background loop tasks"""
    app["show_status_loop"] = asyncio.create_task(show_status_loop())


async def handle(request: aiohttp.ClientRequest):
    """handle request

    Args:
        request (aiohttp.ClientRequest): input request

    Raises:
        e: Exceptrion

    Returns:
        aiohttp.web.Response: response
    """
    pass_forward = True
    async with aiohttp.ClientSession() as session:
        if (
            request.method == "POST"
            and request.content_type == "application/json"
            and str(request.rel_url) == "/v1/chat/completions"
        ):
            pass_forward = False
        data = await request.read()
        start_time = time.perf_counter_ns()
        url: URL = request.url
        new_url = url.with_host(args.llm_host).with_port(args.llm_port)
        async with session.request(
            request.method, new_url, headers=request.headers, data=data
        ) as resp:
            body = await resp.text()
            end_time = time.perf_counter_ns()
            elapsed_time_s = (end_time - start_time) * 1e-9

            web_response = web.Response(body=body, status=resp.status)

            if resp.status != 200 or resp.content_type != "application/json":
                pass_forward = True

            if pass_forward:
                return web_response
            try:
                request_json_body = await request.json()
                model_name = request_json_body["model"]
                resp_json_body = await resp.json()
                usage = resp_json_body["usage"]
                usage["time"] = time.time()
                usage["model"] = model_name
                usage["elapsed_time_s"] = elapsed_time_s
                usage["count"] = 1
                rq = RequestQueue()
                await rq.appendleft(usage)

            except Exception as e:
                print(e)
                raise e
            finally:
                return web_response

# async def clear_status():
#     # clear data
#     print("data cleared")
#     return web.Response(body='cleared', status=200)


if __name__ == "__main__":
    args = parse_args()
    app = web.Application()
    # app.router.add_routes([web.get("/clear",clear_status)])
    app.router.add_route("*", "/{tail:.*}", handle)
    app.on_startup.append(start_background_tasks)
    web.run_app(app, port=args.port)
