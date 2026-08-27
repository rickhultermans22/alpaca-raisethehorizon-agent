"""
mcp_probe.py — proves the project talks to Alpaca through its MCP server, not just the
raw SDK. Connects as a stdio MCP client, lists the tools the server exposes, and calls
get_account + get_option_chain for one ticker. Places no order.

This is the compliance-critical piece: the hackathon requires the Trading API *and*
either Alpaca's MCP server or CLI. scan.py talks to the SDK directly (fine for research);
this script and agent.py talk through the MCP server, which is what the submission
actually runs on.

Usage:  python mcp_probe.py
"""
import asyncio
import json
import os
import shutil

from dotenv import load_dotenv

load_dotenv()

ALPACA_MCP_CMD = os.environ.get(
    'ALPACA_MCP_CMD',
    r'C:\Users\rickh\AppData\Local\Python\pythoncore-3.14-64\Scripts\alpaca-mcp-server.exe',
)
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if not os.path.exists(ALPACA_MCP_CMD):
        raise SystemExit(f'alpaca-mcp-server not found at {ALPACA_MCP_CMD} — set ALPACA_MCP_CMD.')

    params = StdioServerParameters(command=ALPACA_MCP_CMD, args=['--env-file', ENV_FILE])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f'=== {len(names)} tools exposed by the Alpaca MCP server ===')
            for n in names:
                print(f'  {n}')

            print('\n=== get_account_info() ===')
            acc = await session.call_tool('get_account_info', {})
            for block in acc.content:
                if hasattr(block, 'text'):
                    print(block.text[:800])

            print('\n=== get_option_chain(AVGO) ===')
            chain = await session.call_tool('get_option_chain', {
                'underlying_symbol': 'AVGO',
                'expiration_date_gte': '2026-09-03',
                'expiration_date_lte': '2026-09-11',
            })
            for block in chain.content:
                if hasattr(block, 'text'):
                    print(block.text[:1500])


if __name__ == '__main__':
    asyncio.run(main())
