import os
import psycopg2
import boto3
from botocore.config import Config
from dotenv import load_dotenv
import json

load_dotenv()

# S3 setup
s3_id = os.environ.get("BACKBLAZE_KEY_ID")
s3_key = os.environ.get("BACKBLAZE_APP_KEY")
bucket_name = os.environ.get("BACKBLAZE_BUCKET", "wellring-recordings")
endpoint_url = os.environ.get("BACKBLAZE_ENDPOINT_URL", "https://s3.us-east-005.backblazeb2.com")

s3 = boto3.client(
    "s3",
    endpoint_url=endpoint_url,
    aws_access_key_id=s3_id,
    aws_secret_access_key=s3_key,
    config=Config(signature_version="s3v4")
)

# DB setup
db_url = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(db_url)
cur = conn.cursor()

# Get all assessments
cur.execute("SELECT assessment_id, user_id, intent, symptoms, severity, confidence, score, base_score, risk_level, category, action, message, steps, breakdown, bolna_call_id, recording_url, transcript, emotion_analysis, assessed_at FROM assessments;")
cols = [desc[0] for desc in cur.description]
assessments = [dict(zip(cols, row)) for row in cur.fetchall()]

# Get all users
cur.execute("SELECT user_id, clerk_id, name, phone, age, medical_conditions, medications, medical_notes FROM users;")
cols_users = [desc[0] for desc in cur.description]
users = {u['phone']: u for u in [dict(zip(cols_users, row)) for row in cur.fetchall()]}
users_by_id = {u['user_id']: u for u in users.values()}

# List B2 objects
response = s3.list_objects_v2(Bucket=bucket_name)
b2_objects = response.get('Contents', [])

matched_data = []

for obj in b2_objects:
    key = obj['Key']
    size = obj['Size']
    last_modified = obj['LastModified'].isoformat()
    
    # Generate presigned URL (24 hour expiry)
    presigned_url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket_name, 'Key': key},
        ExpiresIn=86400
    )
    
    # Extract phone from key (e.g. recordings/918421971145/2026/07/31/...)
    parts = key.split('/')
    phone = None
    if len(parts) >= 2 and parts[0] == 'recordings':
        phone = parts[1]
    
    # Let's find a matching assessment or user
    matched_user = None
    if phone:
        # try standard match
        clean_phone = phone
        if not clean_phone.startswith('+'):
            clean_phone = '+' + clean_phone
        matched_user = users.get(clean_phone)
        if not matched_user:
            # try suffix/prefix match
            for u_phone, u_val in users.items():
                if u_phone and (u_phone.replace('+', '') == phone or phone in u_phone):
                    matched_user = u_val
                    break
    
    # Find matching assessment
    # A recording is permanent: endpoint_url/bucket/key
    full_b2_url = f"{endpoint_url.rstrip('/')}/{bucket_name}/{key}"
    matching_assess = None
    for assess in assessments:
        if assess['recording_url'] == full_b2_url:
            matching_assess = assess
            break
            
    if not matching_assess and phone:
        # Match by phone number and closest date if no exact URL match
        # Let's filter assessments for this user
        user_id = matched_user['user_id'] if matched_user else None
        user_assessments = [a for a in assessments if a['user_id'] == user_id]
        if user_assessments:
            # Sort by time
            user_assessments.sort(key=lambda x: x['assessed_at'], reverse=True)
            # Pick the most recent one (or match by date/time if possible)
            matching_assess = user_assessments[0]
            
    matched_data.append({
        'key': key,
        'size': size,
        'last_modified': last_modified,
        'presigned_url': presigned_url,
        'full_b2_url': full_b2_url,
        'phone': phone,
        'user': matched_user,
        'assessment': matching_assess
    })

print(f"Matched {len(matched_data)} recordings.")
with open('scratch/matched_recordings.json', 'w') as f:
    json.dump(matched_data, f, default=str, indent=2)

conn.close()
