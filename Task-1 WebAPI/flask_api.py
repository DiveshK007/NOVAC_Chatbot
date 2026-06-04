from flask import Flask, render_template, request, jsonify
from pymongo import MongoClient
import os
from nltk.tokenize import sent_tokenize
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from pypdf import PdfReader

app = Flask(__name__)

model = SentenceTransformer('all-MiniLM-L6-v2')

client = MongoClient("mongodb://localhost:27017/")
db = client["novac_db"]
collection = db["chunks"]

# Home
@app.route('/')
def home():
    return render_template("flaskapi.html")

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files['file']
    os.makedirs("uploads", exist_ok=True)
    filepath = os.path.join("uploads", file.filename)
    file.save(filepath)
    if file.filename.endswith(".txt"):
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
    elif file.filename.endswith(".pdf"):
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
    else:
        return jsonify({
            "message": "Only TXT and PDF files are supported"
        })
    sentences = sent_tokenize(text)
    if len(sentences) == 0:
        return jsonify({
            "message": "Empty file"
        })
    embeddings = model.encode(sentences)
    chunks = []
    current_chunk = sentences[0]

    # Chunking
    for i in range(1, len(sentences)):

        similarity = cosine_similarity(

            [embeddings[i - 1]],
            [embeddings[i]]

        )[0][0]
        if similarity > 0.5:

            current_chunk += " " + sentences[i]
        else:
            chunks.append(current_chunk)
            current_chunk = sentences[i]
    chunks.append(current_chunk)
    saved_chunks = []

    #MongoDB
    for idx, chunk in enumerate(chunks):
        embedding = model.encode(chunk).tolist()
        document = {
            "chunk_id": idx + 1,
            "filename": file.filename,
            "chunk": chunk,
            "embedding": embedding
        }
        collection.insert_one(document)
        saved_chunks.append({
            "chunk_id": idx + 1,
            "chunk": chunk
        })
    return jsonify({
        "message": "File uploaded successfully",
        "chunks": saved_chunks
    })
if __name__ == '__main__':
    app.run(debug=True)