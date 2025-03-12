import os
import threading
import psycopg2
import base64
from psycopg2 import pool
import barcode
from barcode.writer import ImageWriter
from flask import Flask, request, jsonify
from supabase import create_client
from dotenv import load_dotenv
import qrcode
import time
from PIL import Image

load_dotenv()

app = Flask(__name__)

# Supabase Database Connection Pool
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "sslmode": "disable"
}

db_pool = pool.SimpleConnectionPool(1, 10, **DB_CONFIG)

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = "barcodes_new"
QR_SUPABASE_BUCKET = "qrcodes_new"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_db_connection():
    return db_pool.getconn()

def release_db_connection(conn):
    db_pool.putconn(conn)

def generate_unique_id(name):
    return f"{name}{int(time.time() * 1000)}"  # Unique timestamp-based ID

def generate_barcode(name):
    """Generates a Code-128 barcode and saves it to /tmp."""
    try:
        unique_id = generate_unique_id(name)
        save_dir = "/tmp"
        os.makedirs(save_dir, exist_ok=True)  # Ensure /tmp directory exists

        barcode_path = f"{save_dir}/{unique_id}"
        code128 = barcode.get_barcode_class('code128')
        barcode_instance = code128(unique_id, writer=ImageWriter())
        full_path = barcode_instance.save(barcode_path)  # Save returns actual file path

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Barcode image was not created: {full_path}")

        return full_path, unique_id
    except Exception as e:
        print(f"Error generating barcode: {e}")
        return None, None

def generate_qr_code(name):
    """Generates a QR code and saves it to /tmp."""
    try:
        unique_id = generate_unique_id(name)
        qr_path = f"/tmp/{unique_id}.png"
        qr = qrcode.make(unique_id)
        qr.save(qr_path)
        return qr_path, unique_id
    except Exception as e:
        print(f"Error generating QR Code: {e}")
        return None, None


def convert_to_jpeg(image_path):
    """Converts PNG image to a smaller JPEG."""
    try:
        image = Image.open(image_path)
        jpeg_path = image_path.replace(".png", ".jpg")
        image = image.convert("RGB")  # Convert PNG with alpha to RGB
        image.save(jpeg_path, "JPEG", quality=80)  # Save with compression
        return jpeg_path
    except Exception as e:
        print(f"Error converting to JPEG: {e}")
        return None


def image_to_base64(image_path):
    """Converts an image file to a Base64 string."""
    try:
        jpeg_path = convert_to_jpeg(image_path)  # Resize and convert image to JPEG
        if not jpeg_path:
            return None
        with open(jpeg_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"Error converting image to base64: {e}")
        return None
    

def upload_to_supabase(image_path, unique_id, bucket):
    try:
        if not os.path.exists(image_path):
            print(f"Error: File not found before upload: {image_path}")
            return None

        print(f"Uploading file: {image_path}")  # Debugging log

        with open(image_path, "rb") as f:
            supabase.storage.from_(bucket).upload(f"static/{unique_id}.png", f, {"content-type": "image/png"})
        
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/static/{unique_id}.png"
    except Exception as e:
        print(f"Error uploading to Supabase: {e}")
        return None



def store_barcode_in_db(name, unique_id, barcode_url, barcode_path):
    try:
        barcode_base64 = image_to_base64(barcode_path)
        if not barcode_base64:
            return False

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products_new (name, unique_id, barcode_image_path, barcode_image_base64) VALUES (%s, %s, %s, %s)",
            (name, unique_id, barcode_url, barcode_base64)
        )
        conn.commit()
        cur.close()
        release_db_connection(conn)
        return True
    except Exception as e:
        print(f"Database Error (Barcode): {e}")
        return False


def store_qr_in_db(name, unique_id, qr_url, qr_path):
    try:
        qr_base64 = image_to_base64(qr_path)
        if not qr_base64:
            return False

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO qr_codes_new (name, unique_id, qr_code_image_path, qr_code_image_base64) VALUES (%s, %s, %s, %s)",
            (name, unique_id, qr_url, qr_base64)
        )
        conn.commit()
        cur.close()
        release_db_connection(conn)
        return True
    except Exception as e:
        print(f"Database Error (QR Code): {e}")
        if conn:
            conn.rollback()  # 🚀 Ensure transaction rollback
        return False



@app.route('/generate_barcode', methods=['POST'])
def generate_barcode_api():
    data = request.json
    name = data.get("name")
    quantity = data.get("quantity")

    if not name or not quantity:
        return jsonify({"isSuccess": False, "message": "Missing required fields"}), 400

    barcode_urls = []
    for _ in range(quantity):
        barcode_path, unique_id = generate_barcode(name)
        if not barcode_path:
            return jsonify({"isSuccess": False, "message": "Failed to generate barcode"}), 500

        barcode_url = upload_to_supabase(barcode_path, unique_id, SUPABASE_BUCKET)
        if not barcode_url:
            return jsonify({"isSuccess": False, "message": "Failed to upload barcode"}), 500

        barcode_urls.append({"unique_id": unique_id, "barcode_image_path": barcode_url})

    return jsonify({"isSuccess": True, "message": "Barcodes generated", "barcodes": barcode_urls}), 201

@app.route('/generate_qrcode', methods=['POST'])
def generate_qr_api():
    data = request.json
    name = data.get("name")
    quantity = data.get("quantity")

    if not name or not quantity:
        return jsonify({"isSuccess": False, "message": "Missing required fields"}), 400

    qr_urls = []
    for _ in range(quantity):
        qr_path, unique_id = generate_qr_code(name)
        if not qr_path:
            return jsonify({"isSuccess": False, "message": "Failed to generate QR Code"}), 500

        qr_url = upload_to_supabase(qr_path, unique_id, QR_SUPABASE_BUCKET)
        if not qr_url:
            return jsonify({"isSuccess": False, "message": "Failed to upload QR Code"}), 500

        if not store_qr_in_db(name, unique_id, qr_url):
            return jsonify({"isSuccess": False, "message": "Failed to store QR Code in database"}), 500

        qr_urls.append({"unique_id": unique_id, "qr_code_image_path": qr_url})

    return jsonify({"isSuccess": True, "message": "QR Codes generated", "qr_codes": qr_urls}), 201

#-------------------------------------------------------------------------------------------------------------------

def generate_unique_id_new(data):
    return f"{abs(hash(data))}{int(time.time() * 1000)}"  # Ensure hash value is positive

# Barcode Generation

def generate_barcode_new(data):
    try:
        unique_id = generate_unique_id_new(data)
        save_dir = "/tmp"
        os.makedirs(save_dir, exist_ok=True)

        barcode_path = f"{save_dir}/{unique_id}.png"  # Save as PNG first
        code128 = barcode.get_barcode_class('code128')
        barcode_instance = code128(data, writer=ImageWriter())
        full_path = barcode_instance.save(barcode_path)  # Now the file exists

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Barcode image was not created: {full_path}")

        # Convert to JPEG AFTER the barcode is generated
        jpeg_path = convert_to_jpeg(full_path)
        if jpeg_path:
            return jpeg_path, unique_id
        return full_path, unique_id  # Return PNG if JPEG conversion fails
    except Exception as e:
        print(f"Error generating barcode: {e}")
        return None, None



# def generate_barcode_new(data):
#     try:
#         unique_id = generate_unique_id_new(data)
#         save_dir = "/tmp"
#         os.makedirs(save_dir, exist_ok=True)

#         barcode_path = f"{save_dir}/{unique_id}"
#         barcode_path = convert_to_jpeg(barcode_path)
#         code128 = barcode.get_barcode_class('code128')
#         barcode_instance = code128(data, writer=ImageWriter())
#         full_path = barcode_instance.save(barcode_path)

#         if not os.path.exists(full_path):
#             raise FileNotFoundError(f"Barcode image was not created: {full_path}")

#         return full_path, unique_id
#     except Exception as e:
#         print(f"Error generating barcode: {e}")
#         return None, None


# QR Code Generation
def generate_qr_code_new(data):
    try:
        unique_id = generate_unique_id_new(data)
        save_dir = "/tmp"
        os.makedirs(save_dir, exist_ok=True)

        qr_path = f"{save_dir}/{unique_id}.png"  # Save as PNG first
        qr = qrcode.make(data)
        qr.save(qr_path)

        # Convert to JPEG AFTER the QR code is generated
        jpeg_path = convert_to_jpeg(qr_path)
        if jpeg_path:
            return jpeg_path, unique_id
        return qr_path, unique_id  # Return PNG if JPEG conversion fails
    except Exception as e:
        print(f"Error generating QR Code: {e}")
        return None, None



# def generate_qr_code_new(data):
#     try:
#         unique_id = generate_unique_id_new(data)
#         qr_path = f"/tmp/{unique_id}.png"
#         qr_path = convert_to_jpeg(qr_path)
#         qr = qrcode.make(data)
#         qr.save(qr_path)
#         return qr_path, unique_id
#     except Exception as e:
#         print(f"Error generating QR Code: {e}")
#         return None, None

@app.route('/generate_barcode_v2', methods=['POST'])
def generate_barcode_api_v2():
    data = request.json
    value = data.get("value")

    if not value:
        return jsonify({"isSuccess": False, "message": "Missing required field: text"}), 400

    barcode_path, unique_id = generate_barcode_new(value)
    if not barcode_path:
        return jsonify({"isSuccess": False, "message": "Failed to generate barcode"}), 500

    barcode_url = upload_to_supabase(barcode_path, unique_id, SUPABASE_BUCKET)
    if not barcode_url:
        return jsonify({"isSuccess": False, "message": "Failed to upload barcode"}), 500

    store_barcode_in_db(value, unique_id, barcode_url, barcode_path)

    barcode_base64 = image_to_base64(barcode_path)

    return jsonify({"isSuccess": True, "message": "Barcode generated", "barcode": {"unique_id": unique_id, "barcode_image_path": barcode_url, "barcode_image_base64": barcode_base64}}), 201


@app.route('/generate_qrcode_v2', methods=['POST'])
def generate_qr_api_v2():
    data = request.json
    value = data.get("value")

    if not value:
        return jsonify({"isSuccess": False, "message": "Missing required field: text"}), 400

    qr_path, unique_id = generate_qr_code_new(value)
    if not qr_path:
        return jsonify({"isSuccess": False, "message": "Failed to generate QR Code"}), 500

    qr_url = upload_to_supabase(qr_path, unique_id, QR_SUPABASE_BUCKET)
    if not qr_url:
        return jsonify({"isSuccess": False, "message": "Failed to upload QR Code"}), 500

    store_qr_in_db(value, unique_id, qr_url, qr_path)

    qr_base64 = image_to_base64(qr_path)

    return jsonify({"isSuccess": True, "message": "QR Code generated", "qr_code": {"unique_id": unique_id, "qr_code_image_path": qr_url, "qr_code_image_base64": qr_base64}}), 201


# @app.route('/generate_barcode_v2', methods=['POST'])
# def generate_barcode_api_v2():
#     data = request.json
#     value = data.get("value")

#     if not value:
#         return jsonify({"isSuccess": False, "message": "Missing required field: text"}), 400

#     barcode_path, unique_id = generate_barcode_new(value)
#     if not barcode_path:
#         return jsonify({"isSuccess": False, "message": "Failed to generate barcode"}), 500

#     barcode_url = upload_to_supabase(barcode_path, unique_id, SUPABASE_BUCKET)
#     if not barcode_url:
#         return jsonify({"isSuccess": False, "message": "Failed to upload barcode"}), 500

#     store_barcode_in_db(value, unique_id, barcode_url, barcode_path)

#     return jsonify({"isSuccess": True, "message": "Barcode generated", "barcode": {"unique_id": unique_id, "barcode_image_path": barcode_url}}), 201


# @app.route('/generate_qrcode_v2', methods=['POST'])
# def generate_qr_api_v2():
#     data = request.json
#     value = data.get("value")

#     if not value:
#         return jsonify({"isSuccess": False, "message": "Missing required field: text"}), 400

#     qr_path, unique_id = generate_qr_code_new(value)
#     if not qr_path:
#         return jsonify({"isSuccess": False, "message": "Failed to generate QR Code"}), 500

#     qr_url = upload_to_supabase(qr_path, unique_id, QR_SUPABASE_BUCKET)
#     if not qr_url:
#         return jsonify({"isSuccess": False, "message": "Failed to upload QR Code"}), 500

#     store_qr_in_db(value, unique_id, qr_url, qr_path)

#     return jsonify({"isSuccess": True, "message": "QR Code generated", "qr_code": {"unique_id": unique_id, "qr_code_image_path": qr_url}}), 201



if __name__ == '__main__':
    app.run(port=5001, threaded=True)


