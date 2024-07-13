from telegram import Update, ForceReply
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

Base = declarative_base()

class GymNote(Base):
    __tablename__ = 'gym_notes'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    note = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

engine = create_engine('sqlite:///gym_notes.db')
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)
session = Session()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text('Hi! Use /note to add a gym note.')

async def note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the gym note to the database."""
    user_id = update.message.from_user.id
    note = update.message.text[len('/note '):]  # Remove the command part

    gym_note = GymNote(user_id=user_id, note=note)
    session.add(gym_note)
    session.commit()

    await update.message.reply_text('Note saved!')

def main() -> None:
    """Start the bot."""
    application = ApplicationBuilder().token('').build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("note", note_handler))

    application.run_polling()

if __name__ == '__main__':
    main()
