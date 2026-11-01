# python_client

Hub Python client (`HubClient`, `SlicerHubClient`, event-name helpers).

## Install

From `HubInterface/`:

```bash
pip install -e python_client
```

## Live smokes

Shared hub / client / service-provider smokes live under [`../Testing`](../Testing/README.md):

```bash
# terminal A
cd hub && python hub.py --port 2018

# terminal B
cd HubInterface
python Testing/Python/run_live_smokes.py
```

## Minimal example (local hub)

```python
import asyncio
from hub_client import (
    HubClientOptions,
    HubConfig,
    SessionConfig,
    SlicerHubClient,
    generate_subscriber_name,
    hub_event_name,
)

HUB = HubConfig(
    hub_endpoint="http://127.0.0.1:2018/api/hub",
    authorization_endpoint="http://127.0.0.1:2018/oauth/authorize",
    token_endpoint="http://127.0.0.1:2018/oauth/token",
    client_id="130c3d9c-4157-4dd1-aa1d-slicer",
    client_secret="client_secret_3d_Slicer",  # public mock-OAuth placeholder
)


async def main() -> None:
    session = SessionConfig(
        topic="USER-1",
        subscriber_name=generate_subscriber_name("DEMO"),
        product_name="DEMO",
        product_version="1.0",
        actors=["ID"],
        events=["*"],
        user_name="demo-user",
    )
    client = SlicerHubClient(
        HUB,
        session,
        HubClientOptions(auto_reconnect=True),
    )
    await client.connect()
    print(f"listening as {client.subscriber_name} on {client.topic}")
    try:
        while True:
            msg = await client.message_queue.get()
            print("event:", hub_event_name(msg), "id:", msg.get("id"))
    finally:
        await client.close(hub_unsubscribe=True)


if __name__ == "__main__":
    asyncio.run(main())
```
