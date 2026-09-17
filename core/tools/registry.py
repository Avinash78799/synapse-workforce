from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class InvestigateArgs(Arguments):
    customer_id: str = Field(pattern=r'^CUS-[0-9]+$')
    order_id: str = Field(pattern=r'^ORD-[0-9]+$')


class EntityArgs(Arguments):
    entity_id: str = Field(min_length=1, max_length=100)


class RefundArgs(Arguments):
    customer_id: str
    order_id: str
    payment_id: str
    amount_minor: int = Field(gt=0, le=100_000_000)
    currency: Literal['INR']
    duplicate_verified: bool
    claim_count: int = Field(ge=0)


class RetryArgs(Arguments):
    entity_id: str
    retry_eligible: bool
    retry_count: int = Field(ge=0)


class TaskArgs(Arguments):
    entity_id: str
    title: str = Field(max_length=300)
    priority: Literal['normal','high'] = 'normal'


class TicketArgs(Arguments):
    entity_id: str
    status: Literal['resolved','escalated']
    note: str = Field(max_length=1000)


class MessageArgs(Arguments):
    customer_id: str
    order_id: str
    text: str = Field(max_length=1200)


@dataclass(frozen=True)
class Tool:
    name: str
    agent: str
    skill: str
    arguments: type[BaseModel]
    mutates: bool = False


TOOLS = {t.name: t for t in [
    Tool('operations.inspect','operations','OrderSkill',Arguments),
    Tool('support.investigate','support','PaymentSkill',InvestigateArgs),
    Tool('payment.retry','operations','PaymentSkill',RetryArgs,True),
    Tool('shipment.follow_up','operations','ShipmentSkill',EntityArgs,True),
    Tool('task.create','operations','TaskSkill',TaskArgs,True),
    Tool('operations.escalate','operations','EscalationSkill',TaskArgs,True),
    Tool('payment.refund','support','RefundSkill',RefundArgs,True),
    Tool('ticket.update','support','TicketSkill',TicketArgs,True),
    Tool('support.notify','support','CommunicationSkill',MessageArgs,True),
]}


def tool_catalog(kind):
    return [t for t in TOOLS.values() if t.agent == kind]
