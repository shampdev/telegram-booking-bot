from aiogram.fsm.state import State, StatesGroup


class Booking(StatesGroup):
    service = State()
    master = State()
    day = State()
    time = State()
    name = State()
    phone = State()
    confirm = State()


class Review(StatesGroup):
    comment = State()


class Broadcast(StatesGroup):
    text = State()
    confirm = State()


class AdminForm(StatesGroup):
    service_name = State()
    service_price = State()
    service_duration = State()
    service_description = State()
    service_new_price = State()
    master_name = State()
    master_description = State()
