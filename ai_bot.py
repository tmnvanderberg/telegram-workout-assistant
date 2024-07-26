import os
import json
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackContext
from sqlalchemy import create_engine, Column, Integer, String, DateTime, select, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import openai
from config import OPENAI_API_KEY, TELEGRAM_BOT_TOKEN

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Set up SQLAlchemy
Base = declarative_base()

class GymNote(Base):
    __tablename__ = 'gym_notes'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    note = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Ensure the database file is in the correct location
db_path = 'sqlite:///gym_notes.db'

engine = create_engine(db_path)
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)
session = Session()

# Set up OpenAI
openai.api_key = OPENAI_API_KEY

async def error_handler(update: object, context: CallbackContext):
    # Log the error before we do anything else, so we can see it even if something breaks.
    print(f'Update {update} caused error {context.error}')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text('Hi! Use /note to add a gym note or /query to ask a question.')

async def parse_gym_note(note: str) -> dict:
    """Use OpenAI to parse the gym note, extract relevant information, and identify missing details."""

    content = (

    )

    logger.info("Sending request to OpenAI to parse gym note")
    response = await openai.ChatCompletion.acreate(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": """You are a helpful gym assistant. Users will provide gym notes to you,
     describing exercises they performed. Your task is to parse this info and
      return it in a standardized JSON object with the following fields:
       exercise: name or description of exercise
       sets: integer, number of sets performed
       reps: array of reps for each sets
       weight: array with weight for each set
       duration: time spent in total, usually only for cardio exercises
       notes: some general extra notes in a string \n
       For example:
      '{"exercise": "low cable pull", "sets": 4, "reps": [10, 12, 12, 12], "weight": [5, 10, 15, 15], '
      '"duration": "", "notes": "easy"}\n'
      If no information is available for a certain field, just leave it empty. Return only
      the JSON string with no additional comments or notes. Convert all fields to standard
      SI units (when applicable) and lowercase the name of the exercise. Since the notes can
      have multiple exercises, always return an array of objects in the JSON string."""
            },
            {
                "role": "user",
                "content": (
                    f"Process the following gym notes."
                    f"Note: {note}\n\n  "
                )
            }
        ]
    )
    parsed_text = response.choices[0].message['content'].strip()
    logger.info(f"Received response from OpenAI: {parsed_text}")

    try:
        parsed_notes = json.loads(parsed_text)
    except json.JSONDecodeError as e:
        err_str = f"Failed to parse JSON from the model's response: {str(e)}"
        logger.error(err_str)
        raise ValueError(err_str)

    return parsed_notes

async def note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the gym note to the database."""
    user_id = update.message.from_user.id
    note = update.message.text[len('/note '):]  # Remove the command part

    # Parse the gym note using OpenAI API
    parsed_notes = await parse_gym_note(note)

    # Save the parsed note to the database
    for exercise in parsed_notes:
      gym_note = GymNote(user_id=user_id, note=json.dumps(exercise))
      session.add(gym_note)
      session.commit()

      # Inform the user about the saved details
      saved_details = "\n".join([f"{key}: {value}" for key, value in exercise.items()])
      await update.message.reply_text(f'Note saved with the following details:\n{saved_details}')
      logger.info(f'Note saved for user {user_id} with details: {saved_details}')

async def query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /query command by forwarding the user's prompt to OpenAI and executing the generated query."""
    user_id = update.message.from_user.id
    prompt = update.message.text[len('/query '):]  # Remove the command part

    # Describe the database schema to the model
    db_schema = """
    A database has a table named 'gym_notes' with the following columns:
    - id (Integer, primary key)
    - user_id (Integer)
    - note (String)
    - timestamp (DateTime)
    The field contains a JSON string describing a (gym) exercise set. It has the fields:
      exercise: name or description of exercise
      sets: integer, number of sets performed
      reps: array of reps for each sets
      weight: array with weight for each set
      duration: time spent in total, usually only for cardio exercises
      notes: some general extra notes in a string
    For example:
    {"exercise": "low cable pull", "sets": 4, "reps": [10, 12, 12, 12], "weight": [5, 10, 15, 15], "duration": "", "notes": ""}
    """

    logger.info("Sending request to OpenAI to generate a SQL query")
    # Use OpenAI to generate a SQL query based on the user's prompt
    response = await openai.ChatCompletion.acreate(
        model="gpt-4",
        messages=[
            {"role": "system", "content": f"You are a helpful assistant. {db_schema}"},
            {"role": "user", "content": f" Given user ID = {user_id}. Generate an SQL query to fetch data from the gym_notes table that could help to answer the questions in the user prompt. Make sure you follow the specified schema to generate a correct query. [prompt: {prompt}] Return only the raw SQL query, without any comments or remarks, so it can be used directly to query the SQLite database."}
        ]
    )
    sql_query = response.choices[0].message['content'].strip()
    logger.info(f"Generated SQL query from OpenAI: {sql_query}")

    summary = ""
    # Execute the generated SQL query
    try:
        result = session.execute(text(sql_query))
        result_rows = result.fetchall()
        if not result_rows:
            logger.info("Query returned no results.")
            result_text = "Query returned no results."
        else:
            result_text = "\n".join(str(row) for row in result_rows)
            logger.info(f"Query results: {result_text}")

            logger.info("Sending query results to OpenAI for summarization")
            # Summarize the results using OpenAI
            summary_response = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Your task is to help a user with exercise questions. You will receive data from a database of exercises the user has performed before."},
                    {"role": "user", "content": f" Using data: {result_text}. Answer the question in the user prompt: {prompt}."}
                ]
            )
            summary = summary_response.choices[0].message['content'].strip()
            logger.info(f"Received summary from OpenAI: {summary}")
    except Exception as e:
        logger.error(f"An error occurred while executing the query: {e}")
        summary = f"An error occurred while executing the query: {e}"

    # Return the summarized results to the user
    await update.message.reply_text(f'{summary}')

def main() -> None:
    """Start the bot."""
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_error_handler(error_handler);

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("note", note_handler))
    application.add_handler(CommandHandler("query", query_handler))

    application.run_polling()

if __name__ == '__main__':
    main()
