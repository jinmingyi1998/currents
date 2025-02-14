# Currents

> A single file LLM API throughput monitor

A HTTP proxy for LLM Openai API that monitors the throughput of your LLM API calls, implemented with Python aiohttp.

# Usage

start up a proxy server
```bash
python main.py --llm-host <your llm server host> --llm-port <your llm server port>
```

then send your LLM calls to proxy server
