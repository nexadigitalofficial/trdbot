from google import genai
from google.genai import types

client = genai.Client()
chat = client.chats.create(
    model="gemini-2.5-flash",
    history=[
        types.Content(role="user", parts=[types.Part(text="Hello")]),
        types.Content(
            role="model",
            parts=[
                types.Part(
                    text="Great to meet you. What would you like to know?"
                )
            ],
        ),
    ],
)
response = chat.send_message_stream(message="I have 2 dogs in my house.")
for chunk in response:
    print(chunk.text)
    print("_" * 80)
response = chat.send_message_stream(message="How many paws are in my house?")
for chunk in response:
    print(chunk.text)
    print("_" * 80)

print(chat.get_history())