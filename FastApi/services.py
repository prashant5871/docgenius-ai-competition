from fastapi import HTTPException, status, UploadFile
from sentence_transformers import SentenceTransformer
from db import get_db
from models import User, Chat, Message
from auth import hash_password, verify_password, create_access_token, verify_token
from bson import ObjectId
from datetime import datetime
import os
from dotenv import load_dotenv
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from transformers import pipeline
from typing import List
from utils import embed_text, build_faiss_index, search_faiss, structure_response

# Initialize the Hugging Face summarizer pipeline
summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
embedding_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

# Load environment variables from .env file
load_dotenv()

conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=f"DocGenius AI <{os.getenv('MAIL_USERNAME')}>",
    MAIL_PORT=587,
    MAIL_SERVER="smtp.gmail.com",  # Change for other providers
    MAIL_STARTTLS=True,  # Replaces MAIL_TLS
    MAIL_SSL_TLS=False,  # Replaces MAIL_SSL
    USE_CREDENTIALS=True
)
# User creation (Sign up)
async def create_user(user: User):
    db = get_db()
    # Hash the password before saving to the database
    existing_user = db.users.find_one({"email": user.email})
    print(existing_user)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account with this Email already exists"
        )
    user_data = dict(user)
    user_data["password"] = hash_password(user.password)
    user_data["verify"] = False
    result = db.users.insert_one(user_data)

    token = generate_token(user_data)
    print(token)
    html_body = f"""
<!DOCTYPE html>
<html lang='en'>
<head>
    <meta charset='UTF-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1.0'>
    <title>DocGenius Account Verification</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .email-container {{
            border: 1px solid #e1e1e1;
            border-radius: 5px;
            overflow: hidden;
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            background-color: #4A90E2;
            color: white;
            padding: 20px;
            text-align: center;
        }}
        .content {{
            padding: 20px 30px;
            background-color: #ffffff;
        }}
        .credentials-box {{
            background-color: #f9f9f9;
            border-left: 4px solid #4A90E2;
            padding: 15px;
            margin: 20px 0;
        }}
        .button-container {{
            text-align: center;
            margin: 25px 0 15px;
        }}
        .verify-button {{
            display: inline-block;
            background-color: #4CAF50;
            color: white;
            padding: 12px 25px;
            text-decoration: none;
            border-radius: 4px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-size: 14px;
            transition: background-color 0.3s;
        }}
        .verify-button:hover {{
            background-color: #45a049;
        }}
        .footer {{
            text-align: center;
            padding: 15px;
            font-size: 12px;
            color: #777777;
            background-color: #f7f7f7;
            border-top: 1px solid #e1e1e1;
        }}
        .logo {{
            font-size: 24px;
            font-weight: bold;
            letter-spacing: 1px;
        }}
    </style>
</head>
<body>
    <div class='email-container'>
        <div class='header'>
            <div class='logo'>DocGenius AI</div>
            <p>Email Verification</p>
        </div>
        <div class='content'>
            <p>Dear <strong>{user_data['name']}</strong>,</p>
            <p>Welcome to <strong>DocGenius</strong> – your AI-powered assistant for document-based question answering.</p>
            <p>To activate your account and start using DocGenius, please verify your email by clicking the button below:</p>
            <div class='button-container'>
                <a href="{os.getenv('VARIFY_URL')}/verify/{str(token['access_token'])}" class='verify-button'>Verify Account</a>
            </div>
            <p>If you did not request this registration, you can safely ignore this email.</p>
            <p>Thank you,<br>The DocGenius Team</p>
        </div>
        <div class='footer'>
            <p>© 2025 DocGenius. All rights reserved.</p>
            <p>This is an automated message. Please do not reply.</p>
        </div>
    </div>
</body>
</html>
"""

    message = MessageSchema(
        subject="Verify Your Email for DocGenius AI",
        recipients=[user_data["email"]],
        body=html_body,
        subtype="html"
    )
    fm = FastMail(conf)
    await fm.send_message(message)
    # return str(result.inserted_id)  # Return the user ID as string
    return 

# Authenticate User (Login)
def authenticate_user(email: str, password: str):
    db = get_db()
    user = db.users.find_one({"email": email})
    
    # If user doesn't exist or password is incorrect
    if not user or not verify_password(password, user['password']):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect credentials"
        )
    
    if not user.get('verify', False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your email is not verified. Please check your inbox to verify your account."
        )
    
    # Return the User model object
    # user_data = User(**user)
    chat_ids = user.get("chat_ids", [])
    chats = []
    
    if chat_ids:
        # Query the chats collection using chat_ids
        chats = list(db.chats.find({"_id": {"$in": [ObjectId(chat_id) for chat_id in chat_ids]}}))
        
          # For each chat, populate the messages and associated document metadata
        for chat in chats:
        # For each chat, populate the messages and associated document metadata
         # Fetch the message_ids related to the current chat
            message_ids = chat.get("message_ids", [])
            
            # Fetch the associated messages using the message_ids
            messages = list(db.messages.find({"_id": {"$in": [ObjectId(msg_id) for msg_id in message_ids]}}))
            
            # Add the messages to the chat data and convert ObjectId to string
            chat["messages"] = [
                {
                    "_id": str(message["_id"]),  # Convert ObjectId to string
                    "text": message["text"],
                    "answer": message["answer"],
                    "timestamp": message["timestamp"]
                }
                for message in messages
            ]
            
            
            # Populate document metadata if applicable
            # if chat.get("document_path"):
            #     # Fetch document metadata (for example, document summary, sentences, embeddings)
            #     document_data = {
            #         "document_path": chat["document_path"],
            #         "timestamp": chat["timestamp"],
            #         "type": chat["type"],
            #         "size": chat["size"],
            #         "doc_summary": chat["doc_summary"],
            #         "sentences": chat.get("sentences", []),
            #         "embeddings": chat.get("embeddings", [])
            #     }
            #     chat["document_data"] = document_data
    
    # Prepare the final user data to return
    user_data = {
        "_id": str(user["_id"]),  # Convert ObjectId to string
        "name": user["name"],
        "email": user["email"],
        "verify": user.get("verify", False),
        "chats": [
            {
                "_id": str(chat["_id"]),  # Convert ObjectId to string
                # "chat_name": chat.get("chat_name", "Unnamed Chat"),
                "messages": chat.get("messages", []),  # Include messages if available
                "document_path": chat["document_path"],
                "timestamp": chat["timestamp"],
                "type": chat["type"],
                "size": chat["size"],
                "doc_summary": chat["doc_summary"],
                # "document_data": chat.get("document_data", {})  # Include document metadata
            }
            for chat in chats
        ]
    }

    return user_data

# Generate JWT Token
def generate_token(user_data):
    access_token = create_access_token(data={"user_id": str(user_data["_id"])})
    return {"access_token": access_token, "token_type": "bearer"}

# Utility function to split text into sentences
def split_into_sentences(text: str) -> List[str]:
    return [sent.strip() for sent in text.split('.') if sent.strip()]

# services.py

# The function to clean the extracted text (if needed)
def clean_text(raw_text: str) -> str:
    # A placeholder for any text cleaning logic you might want
    return raw_text.strip()

def summarize_text(text: str) -> str:
    # Use Hugging Face's BART summarizer to generate a summary
    summary = summarizer(text, max_length=500, min_length=50, do_sample=False)
    return summary[0]['summary_text']

async def create_chat(file_size: int,file_extension: str, user_id: str,raw_text: str, document_path: str = None):
    
    # Clean and summarize the extracted text
    cleaned_text = clean_text(raw_text)
    # Summarize the cleaned text using Hugging Face summarizer
    doc_summary = summarize_text(cleaned_text)
    # Split cleaned text into sentences
    sentences = split_into_sentences(cleaned_text)
    # Generate embeddings for each sentence
    sentence_embeddings = embedding_model.encode(sentences)

    # Convert sentence embeddings to a list (for MongoDB compatibility)
    sentence_embeddings_list = sentence_embeddings.tolist()


    db = get_db()
    
    chat_data = {
    "user_id": ObjectId(user_id),  # Storing the user reference (ObjectId)
    "message_ids": [],  # Start with an empty list of message references
    "document_path": document_path,  # Save the document path in the chat
    "timestamp": datetime.utcnow(),  # Set the current timestamp
    "type": file_extension,  # Set the file extension as the type
    "size": int(file_size/1024),  # Store the file size in KB (integer)
    "doc_summary": doc_summary,  # Store the document summary
    "sentences" : sentences,
    "embeddings": sentence_embeddings_list

}

    # Insert the chat into the database
    result = db.chats.insert_one(chat_data)
    
    # Update the user's chat_ids to include the new chat
    db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$push": {"chat_ids": result.inserted_id}}
    )
    
    # Return the created chat with its `_id` and document_path
    return {
        "_id": str(result.inserted_id),  # Ensure _id is serialized as string
        "document_path": document_path,
        "timestamp": chat_data["timestamp"],
        "type": chat_data["type"],
        "size": chat_data["size"],
        "doc_summary": chat_data["doc_summary"],
    }


def send_message(chat_id: str, text: str):
    db = get_db()

    # Fetch chat data and embeddings
    chat = db.chats.find_one({"_id": ObjectId(chat_id)})
    if not chat:
        raise ValueError("Chat not found")

    sentences = chat.get("sentences", [])
    embeddings = chat.get("embeddings", [])
    messages = chat.get("messages", [])

    # Retrieve previous messages for better context
    past_context = " ".join([msg["text"] for msg in messages[-3:]])  # Get last 3 messages
    full_query = past_context + " " + text  # Merge context with the current query

     # Embed the combined query
    query_vector = embed_text(full_query)


    if not sentences or not embeddings:
        raise ValueError("No sentences or embeddings found in chat")
    
    # Convert embeddings to FAISS index
    index = build_faiss_index(embeddings)

    # Search for relevant sentences
    top_indices, _ = search_faiss(index, query_vector, k=3)
    
    top_sentences = [sentences[idx] for idx in top_indices]

    # Generate answer based on relevant sentences
    answer = structure_response(top_sentences)

    # Create a new message document

    message = {
        "text": text,
        "answer": answer,
        "timestamp": datetime.utcnow()
    }
    
    # Insert the message into the database
    result = db.messages.insert_one(message)
    
    # Add the message reference (_id) to the chat's messages array
    db.chats.update_one(
        {"_id": ObjectId(chat_id)},
        {"$push": {"message_ids": result.inserted_id}}
    )
    
    # Return the created message with its _id
    return {
        "id": str(result.inserted_id),
        "text": text,
        "answer": answer,
        "timestamp": datetime.utcnow()
    }

def delete(chat_id: ObjectId, user_id: ObjectId):
    db = get_db()

    # Fetch the chat document to get the message_ids
    chat = db.chats.find_one({"_id": chat_id})
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    message_ids = chat.get("message_ids", [])

    # Step 1: Remove the chat from the user's chat_ids list
    db.users.update_one(
        {"_id": user_id},
        {"$pull": {"chat_ids": chat_id}}
    )

    # Step 2: Delete the chat document
    db.chats.delete_one({"_id": chat_id})

    # Step 3: Delete all the messages related to this chat using the message_ids
    if message_ids:
        db.messages.delete_many({"_id": {"$in": [ObjectId(mid) for mid in message_ids]}})

    return {"message": "Chat and associated messages deleted successfully"}

def verify_user(token: str):
     payload = verify_token(token)
     db = get_db()  # Get database connection
     user = db.users.find_one({"_id": ObjectId(payload["user_id"])})
     print(user)
     if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
     if user['verify']:
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Already Verified User !"
        )
     db.users.update_one({"_id": ObjectId(payload['user_id'])}, {"$set": {"verify": True}})
     return User(**user)