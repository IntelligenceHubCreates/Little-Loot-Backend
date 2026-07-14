from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import DeclarativeBase

# RUN COMMANDS
# alembic revision --autogenerate -m "Added new column"
# alembic upgrade head
# To apply changes to database

class Base(DeclarativeBase):
    pass


class Greeting(Base):
    __tablename__ = "greetings"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String)
