from flask import Flask, request, jsonify
from flask_cors import CORS
import joblib
import json
import math
import os

from commentsReceiver import fetch_comments, CommentFetchError
from upload_to_snowflake import upload_training_data
from train_model_snowflake import train_model

# --- PATHS RELATIVE TO THIS FILE ---

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_PATH = os.path.join(BASE_DIR, "data_labeled.json")
MODEL_PATH = os.path.join(BASE_DIR, "comment_classifier.pkl")

app = Flask(__name__)
CORS(app)


@app.route("/analyze", methods=["POST"])
def analyze_event():
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"message": "Missing event URL"}), 400

    event_url = data["url"]
    print("Received URL:", event_url)

    # 1) Get comments for this event. They stay in memory, so two requests
    #    at the same time can't overwrite each other's data.
    try:
        all_comments = fetch_comments(event_url)
    except CommentFetchError as e:
        return jsonify({"message": f"Could not load comments: {e}"}), 502

    # 2) Nothing to analyze
    if not all_comments:
        return jsonify({"message": "This event has no comments to analyze yet."})

    # 3) Load model from the same folder as this file
    if not os.path.exists(MODEL_PATH):
        return jsonify({"message": "Model file comment_classifier.pkl not found."}), 500

    model = joblib.load(MODEL_PATH)

    emotional_rate = 0
    rational_rate = 0

    # 4) Classify comments
    for item in all_comments:
        # Skip already-labeled comments if any
        if "label" in item and item["label"]:
            continue

        body = item.get("body", "")
        if not body:
            continue

        prediction = model.predict([body])[0]
        item["label"] = prediction

        if prediction == "EMOTIONAL":
            emotional_rate += 1
        elif prediction == "RATIONAL":
            rational_rate += 1

    # 5) Save labeled comments
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_comments, f, indent=4)

    upload_training_data()
    train_model() 

    # 6) Compute emotional vs rational result
    final_rate = emotional_rate + rational_rate
    if final_rate == 0:
        final_msg = "Not enough usable comments to analyze this event."
        return jsonify({"message": final_msg})

    emotional_percent = (emotional_rate / final_rate) * 100
    rational_percent = (rational_rate / final_rate) * 100

    if emotional_percent > rational_percent:
        final_msg = f"From comments provided it seems like the event might be based on Emotions: {math.ceil(emotional_percent)}%"
    elif rational_percent > emotional_percent:
        final_msg = f"From comments provided it seems like the event might be based on Rationalism: {math.ceil(rational_percent)}%"
    else:
        final_msg = "From comments provided it seems like the event is equally Emotional and Rational."

    print("Final message:", final_msg)
    return jsonify({"message": final_msg})


if __name__ == "__main__":
    print("API running at http://127.0.0.1:5000")
    app.run(port=5000)