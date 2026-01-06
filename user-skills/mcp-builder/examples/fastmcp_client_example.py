import asyncio

from fastmcp import Client, FastMCP

# In-memory server (ideal for testing)
server = FastMCP("TestServer")
client = Client(server)

# HTTP server
client = Client("https://example.com/mcp")

# Local Python script
client = Client("my_mcp_server.py")


async def main():
    async with client:
        # Basic server interaction
        _ = await client.ping()

        # List available operations
        _tools = await client.list_tools()
        _resources = await client.list_resources()
        _prompts = await client.list_prompts()

        # Execute operations
        result = await client.call_tool("example_tool", {"param": "value"})
        print(result)


asyncio.run(main())
