"""FSM-состояния диалогов."""

from aiogram.fsm.state import State, StatesGroup


class SplitFlow(StatesGroup):
    link = State()
    price = State()
    address = State()


class PayFlow(StatesGroup):
    proof = State()


class Activation(StatesGroup):
    data = State()


class SupportFlow(StatesGroup):
    message = State()


class RefundFlow(StatesGroup):
    reason = State()
    wallet = State()


class AdminFlow(StatesGroup):
    reject_reason = State()
    refund_reason = State()
    codes = State()
    broadcast = State()
    reply_ticket = State()
    message_user = State()
    ban = State()
