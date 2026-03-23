from fastapi import APIRouter, Request

router = APIRouter()

# Map WhatsApp phone numbers to project IDs.
# Add entries as you connect more projects to WhatsApp.
PHONE_TO_PROJECT: dict[str, str] = {
    # '+447XXXXXXXXXX': 'phloe',
}

DEFAULT_PROJECT = 'phloe'


@router.post("/whatsapp-proxy")
async def whatsapp_proxy(request: Request):
    """
    Receives messages from OpenClaw/WhatsApp.
    Routes to the appropriate CLAW project based on sender number.
    Returns a response in OpenClaw's expected format.
    """
    from api.main import get_agent
    from core.channels.envelope import MessageEnvelope, Channel

    body = await request.json()
    sender: str = body.get('sender', '')
    content: str = body.get('message', body.get('content', ''))

    project_id = PHONE_TO_PROJECT.get(sender, DEFAULT_PROJECT)

    agent = get_agent(project_id)

    envelope = MessageEnvelope(
        content=content,
        channel=Channel.WHATSAPP,
        project_id=project_id,
        # WhatsApp sessions are per-phone-number, persistent across messages
        session_id=f"wa_{sender.replace('+', '').replace(' ', '')}",
    )

    response = await agent.process(envelope)

    return {
        'response': response.content,
        'model': response.model_used,
    }
