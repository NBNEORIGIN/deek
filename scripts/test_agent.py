#!/usr/bin/env python3
"""
Quick sanity test for CLAW agent.
Run this after any config change to verify everything works.

Usage: python scripts/test_agent.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()


async def test():
    from core.agent import ClawAgent
    import json

    config_path = Path('projects/phloe/config.json')
    if not config_path.exists():
        print("❌ projects/phloe/config.json not found")
        return

    config = json.loads(config_path.read_text())

    print(f"CLAW_FORCE_API : {os.getenv('CLAW_FORCE_API', 'true')}")
    print(f"CLAUDE_MODEL   : {os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-5')}")
    print(f"ANTHROPIC_KEY  : {'set' if os.getenv('ANTHROPIC_API_KEY') else '❌ NOT SET'}")
    print()

    agent = ClawAgent(project_id='phloe', config=config)

    from core.channels.envelope import MessageEnvelope, Channel
    import uuid

    # Test 1: Simple question — should use Sonnet
    print("TEST 1: Simple question (expect Sonnet)...")
    envelope = MessageEnvelope(
        content="What is the Phloe platform?",
        channel=Channel.WEB,
        project_id='phloe',
        session_id=str(uuid.uuid4()),
    )
    response = await agent.process(envelope)
    print(f"  Model   : {response.model_used}")
    print(f"  Cost    : ${response.cost_usd:.4f}")
    print(f"  Preview : {response.content[:120]}...")
    print()

    # Test 2: Architecture question — should use Opus
    print("TEST 2: Architecture question (expect Opus)...")
    envelope2 = MessageEnvelope(
        content="What is the best way to structure the community module architecture?",
        channel=Channel.WEB,
        project_id='phloe',
        session_id=str(uuid.uuid4()),
    )
    response2 = await agent.process(envelope2)
    print(f"  Model   : {response2.model_used}")
    print(f"  Cost    : ${response2.cost_usd:.4f}")
    print(f"  Preview : {response2.content[:120]}...")
    print()

    # Test 3: Code task — should use Sonnet
    print("TEST 3: Code task (expect Sonnet)...")
    envelope3 = MessageEnvelope(
        content="Fix the import statement in bookings/models.py",
        channel=Channel.WEB,
        project_id='phloe',
        session_id=str(uuid.uuid4()),
    )
    response3 = await agent.process(envelope3)
    print(f"  Model   : {response3.model_used}")
    print(f"  Cost    : ${response3.cost_usd:.4f}")
    print()

    total = response.cost_usd + response2.cost_usd + response3.cost_usd
    print(f"✓ All tests complete. Total cost: ${total:.4f}")
    print()
    print("Expected: Test 1 → sonnet, Test 2 → opus, Test 3 → sonnet")
    print("If all responses are coherent and models match, CLAW is working.")


if __name__ == '__main__':
    asyncio.run(test())
