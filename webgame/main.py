from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.requests import Request
from typing import Dict
import asyncio
import time

app = FastAPI()

# Підключаємо папку для шаблонів
templates = Jinja2Templates(directory="templates")

# Підключаємо папку для статичних файлів (CSS, JS тощо)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Словник для зберігання кімнат
rooms: Dict[str, Dict] = {}

# Маршрут для створення кімнати
@app.post("/create_room/{room_id}/{word}/{category}")
async def create_room(room_id: str, word: str, category: str):
    if room_id in rooms:
        raise HTTPException(status_code=400, detail="Room already exists.")
    
    # Додаємо кімнату до словника
    rooms[room_id] = {
        "word": word.lower(),
        "category": category,
        "players": [],
        "guessed_letters": set(),
        "remaining_attempts": 7,
        "finished": False,
        "start_time": time.time(),
        "last_move_time": time.time(),
        "current_player_index": 0
    }
    
    return {"message": f"Room '{room_id}' created successfully with the word '{word}' and category '{category}'"}

# Маршрут для отримання списку лобі
@app.get("/lobbies")
async def get_lobbies():
    return {"lobbies": list(rooms.keys())}

# WebSocket для гри
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    if room_id not in rooms:
        await websocket.accept()
        await websocket.send_text("Room doesn't exist.")
        await websocket.close()
        return

    room = rooms[room_id]
    await websocket.accept()

    # Очікуємо, що гравець надішле свій нікнейм першим повідомленням
    nickname = await websocket.receive_text()
    print(f"Player '{nickname}' joined the room '{room_id}'")  # Логування
    if not nickname:
        await websocket.send_text("Nickname is required.")
        await websocket.close()
        return

    # Додаємо гравця до кімнати
    room["players"].append({"websocket": websocket, "nickname": nickname})
    await broadcast_message(room, f"Player '{nickname}' joined the room!")
    await broadcast_player_list(room)

    # Надсилаємо підказку (категорію) перед початком гри
    await websocket.send_text(f"Category: {room['category']}")

    if len(room["players"]) == 1:
        await websocket.send_text(f"Game started! Word: {get_masked_word(room)} Attempts left: {room['remaining_attempts']}")
    else:
        await websocket.send_text("Wait for your turn.")

    try:
        while not room["finished"]:
            # Перевіряємо, чи не вичерпано час
            if time.time() - room["start_time"] > 120:  # 2 хвилини
                room["finished"] = True
                await broadcast_message(room, f"Time's up! The word was: {room['word']}")
                asyncio.create_task(delete_room_after_delay(room_id))
                break

            # Отримуємо поточного гравця
            current_player = room["players"][room["current_player_index"]]
            if current_player["websocket"] == websocket:
                await websocket.send_text("Your turn! Enter a letter.")
                data = await websocket.receive_text()
                if data.startswith("guess"):
                    letter = data.split(":")[1].lower()
                    result = guess_letter(room, letter)
                    room["last_move_time"] = time.time()

                    if is_won(room):
                        room["finished"] = True
                        await broadcast_message(room, f"Game Over! You guessed the word: {room['word']}")
                        asyncio.create_task(delete_room_after_delay(room_id))

                    elif room["remaining_attempts"] <= 0:
                        room["finished"] = True
                        await broadcast_message(room, f"Game Over! You lost. The word was: {room['word']}")
                        asyncio.create_task(delete_room_after_delay(room_id))

                    else:
                        status = "Correct!" if result else "Wrong!"
                        masked_word = get_masked_word(room)
                        attempts = room["remaining_attempts"]
                        await broadcast_message(
                            room,
                            f"{status} Word: {masked_word} | Attempts left: {attempts}",
                        )
                        next_player(room)
            else:
                await websocket.send_text("Wait for your turn.")
                await asyncio.sleep(1)  # Чекаємо 1 секунду перед наступною перевіркою
    except WebSocketDisconnect:
        room["players"] = [player for player in room["players"] if player["websocket"] != websocket]
        await broadcast_message(room, f"Player '{nickname}' left the room.")
        await broadcast_player_list(room)
        if not room["players"]:
            del rooms[room_id]

# Допоміжні функції для гри
def get_masked_word(room):
    return " ".join([letter if letter in room["guessed_letters"] else "_" for letter in room["word"]])

def guess_letter(room, letter):
    if letter in room["guessed_letters"] or letter not in room["word"]:
        room["remaining_attempts"] -= 1
        return False
    room["guessed_letters"].add(letter)
    return True

def is_won(room):
    return all(letter in room["guessed_letters"] for letter in room["word"])

def next_player(room):
    room["current_player_index"] = (room["current_player_index"] + 1) % len(room["players"])

async def broadcast_message(room, message):
    for player in room["players"]:
        try:
            await player["websocket"].send_text(message)
        except WebSocketDisconnect:
            room["players"].remove(player)

async def broadcast_player_list(room):
    player_list = ", ".join([player["nickname"] for player in room["players"]])
    for player in room["players"]:
        try:
            await player["websocket"].send_text(f"Players in room: {player_list}")
        except WebSocketDisconnect:
            room["players"].remove(player)

async def delete_room_after_delay(room_id: str, delay: int = 5):
    await asyncio.sleep(delay)
    if room_id in rooms:
        del rooms[room_id]
        print(f"Room '{room_id}' has been deleted after {delay} seconds.")

# Маршрут для сторінки створення кімнати
@app.get("/create_room")
async def get_create_room(request: Request):
    return templates.TemplateResponse("create_room.html", {"request": request})

# Маршрут для сторінки доступних лобі
@app.get("/lobbies_page")
async def get_lobbies_page(request: Request):
    return templates.TemplateResponse("lobbies.html", {"request": request})

# Маршрут для сторінки гри
@app.get("/guess_word")
async def get_guess_word(request: Request):
    return templates.TemplateResponse("guess_word.html", {"request": request})

@app.get("/")
async def get_guess_word(request: Request):
    return templates.TemplateResponse("wordlie.html", {"request": request})

# Запуск сервера 
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)