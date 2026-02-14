import asyncio, tempfile
from dataclasses import dataclass
from silas.models.agents import AgentResponse, MemoryOp, MemoryOpType, MemoryQuery, MemoryQueryStrategy
from silas.queue.store import DurableQueueStore
from silas.queue.router import QueueRouter
from silas.queue.consumers import ProxyConsumer
from silas.queue.types import QueueMessage

@dataclass
class MockRouteOutput:
    route:str='direct'
    reason:str='mock'

@dataclass
class MockProxyResult:
    output: MockRouteOutput

class MockProxyAgentWithMemory:
    async def run(self, prompt:str, deps=None):
        print('run called')
        output = MockRouteOutput(route='direct')
        output.response = AgentResponse(
            message='direct answer',
            memory_queries=[MemoryQuery(strategy=MemoryQueryStrategy.keyword, query='status')],
            memory_ops=[MemoryOp(op=MemoryOpType.store, content='store this')],
            needs_approval=False,
        )
        return MockProxyResult(output=output)

async def main():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db=f.name
    print('db', db)
    store=DurableQueueStore(db)
    await store.initialize()
    print('initialized')
    router=QueueRouter(store)
    consumer=ProxyConsumer(store, router, MockProxyAgentWithMemory())
    msg = QueueMessage(message_kind='user_message', sender='user', trace_id='trace-memory-response', payload={'text':'hello'})
    await router.route(msg)
    print('routed')
    value = await consumer.poll_once()
    print('poll_once', value)
    got = await store.lease_filtered(queue_name='proxy_queue', filter_trace_id='trace-memory-response', filter_message_kind='agent_response')
    print('got', got)

asyncio.run(main())
