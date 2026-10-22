import asyncio

from models import AgentMessage


class MessageBus:
    """Communication layer: every agent message is logged here and broadcast to SSE subscribers."""

    def __init__(self):
        self.log: list[AgentMessage] = []
        self.subscribers: list[asyncio.Queue] = []

    async def send(self, message: AgentMessage):
        self.log.append(message)
        for queue in list(self.subscribers):
            await queue.put(message)

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue()
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    def get_log(self) -> list[AgentMessage]:
        return self.log

    def clear(self):
        self.log = []
