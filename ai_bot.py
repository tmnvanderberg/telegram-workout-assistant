import os
import json
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    await update.message.reply_text('Hi! Use /note to add a gym note, /suggest to get workout suggestions, or /query to ask a question.')

async def parse_gym_note(note: str) -> dict:
    """Use OpenAI to parse the gym note, extract relevant information, and identify missing details."""
    logger.info("Sending request to OpenAI to parse gym note")
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant. Extract gym note details and identify any missing information logically needed."
            },
            {
                "role": "user",
                "content": (
                    f"Extract the following information from the gym note and return it as a JSON string:\n\n"
                    f"Note: {note}\n\n"
                    f"Fields: Exercise, Sets, Reps, Weight, Duration, Additional Notes.\n\n"
                    f"Only ask for missing information if it is logically needed for the workout. For example, if duration is missing for a treadmill walk, ask for it. "
                    f"If weight is not given for an exercise, assume body weight if applicable. Make sure to return all the information in a structured JSON format."
                )
            }
        ]
    )
    parsed_text = response.choices[0].message['content'].strip()
    logger.info(f"Received response from OpenAI: {parsed_text}")

    # Parse the JSON string
    try:
        parsed_note = json.loads(parsed_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from the model's response.")
        raise ValueError("Failed to parse JSON from the model's response.")

    # Identify any missing information
    missing_info = parsed_note.get("missing_info", [])

    return parsed_note, missing_info

async def ask_for_clarification(update: Update, missing_info: str) -> None:
    """Ask the user for clarification on missing information."""
    await update.message.reply_text(f"Please provide more details about the following: {missing_info}")

async def note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the gym note to the database."""
    user_id = update.message.from_user.id
    note = update.message.text[len('/note '):]  # Remove the command part

    # Parse the gym note using OpenAI API
    parsed_note, missing_info = await parse_gym_note(note)

    # Check for missing information and ask for clarification if needed
    if missing_info:
        missing_fields = ", ".join(missing_info)
        await ask_for_clarification(update, missing_fields)
        return

    # Save the parsed note to the database
    gym_note = GymNote(user_id=user_id, note=json.dumps(parsed_note))  # Convert parsed_note dict to JSON string
    session.add(gym_note)
    session.commit()

    # Inform the user about the saved details
    saved_details = "\n".join([f"{key}: {value}" for key, value in parsed_note.items()])
    await update.message.reply_text(f'Note saved with the following details:\n{saved_details}')
    logger.info(f'Note saved for user {user_id} with details: {saved_details}')

async def query_database(user_id: int, query_prompt: str) -> str:
    """Generate and execute a SQL query based on a user's prompt and return the results."""
    # Describe the database schema to the model
    db_schema = """
    The database has a table named 'gym_notes' with the following columns:
    - id (Integer, primary key)
    - user_id (Integer)
    - note (String)
    - timestamp (DateTime)

    Example query: SELECT * FROM "gym_notes" WHERE user_id = 1;
    """

    logger.info("Sending request to OpenAI to generate a SQL query")
    # Use OpenAI to generate a SQL query based on the user's prompt
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Generate a SQL query based on the user's prompt."},
            {"role": "user", "content": f"Given the database schema below and the user ID {user_id}, generate a SQL query to answer the following question or perform the following task. Return only the SQL query with nothing else, no comments or remarks, so it can be used directly to query the SQLite database.\n\n{db_schema}\n\nPrompt: {query_prompt}"}
        ]
    )
    sql_query = response.choices[0].message['content'].strip()
    logger.info(f"Generated SQL query from OpenAI: {sql_query}")

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
        return result_text
    except Exception as e:
        logger.error(f"An error occurred while executing the query: {e}")
        return f"An error occurred while executing the query: {e}"

async def suggest_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provide suggestions based on a flexible prompt."""
    user_id = update.message.from_user.id
    prompt = update.message.text[len('/suggest '):]  # Remove the command part

    # Query the database based on the user's prompt
    query_results = await query_database(user_id, prompt)

    # Use the query results as context for generating suggestions
    logger.info("Sending query results to OpenAI for suggestions")
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Use the query results as context to provide suggestions."},
            {"role": "user", "content": f"Based on the following query results, provide suggestions:\n\n{query_results}"}
        ]
    )
    suggestions = response.choices[0].message['content'].strip()
    logger.info(f"Received suggestions from OpenAI: {suggestions}")

    # Return the suggestions to the user
    await update.message.reply_text(f'Suggestions based on your query:\n{suggestions}')

async def query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /query command by forwarding the user's prompt to OpenAI and executing the generated query."""
    user_id = update.message.from_user.id
    prompt = update.message.text[len('/query '):]  # Remove the command part

    # Describe the database schema to the model
    db_schema = """
    The database has a table named 'gym_notes' with the following columns:
    - id (Integer, primary key)
    - user_id (Integer)
    - note (String)
    - timestamp (DateTime)

    Example query: SELECT * FROM "gym_notes" WHERE user_id = 1;
    """

    logger.info("Sending request to OpenAI to generate a SQL query")
    # Use OpenAI to generate a SQL query based on the user's prompt
    response = await openai.ChatCompletion.acreate(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Generate a SQL query based on the user's prompt."},
            {"role": "user", "content": f"Given the database schema below and the user ID {user_id}, generate a SQL query to answer the following question or perform the following task. Return only the SQL query with nothing else, no comments or remarks, so it can be used directly to query the SQLite database.\n\n{db_schema}\n\nPrompt: {prompt}"}
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
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Feel free to add some observations. "},
                    {"role": "user", "content": f"Format the following query result in a compact and human readable way. Feel free to add some observations or suggestions. :\n\n{result_text}"}
                ]
            )
            summary = summary_response.choices[0].message['content'].strip()
            logger.info(f"Received summary from OpenAI: {summary}")
    except Exception as e:
        logger.error(f"An error occurred while executing the query: {e}")
        summary = f"An error occurred while executing the query: {e}"

    # Return the summarized results to the user
    await update.message.reply_text(f'Summary of your query results:\n{summary}')

def main() -> None:
    """Start the bot."""
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("note", note_handler))
    application.add_handler(CommandHandler("suggest", suggest_handler))
    application.add_handler(CommandHandler("query", query_handler))

    application.run_polling()

if __name__ == '__main__':
    main()
