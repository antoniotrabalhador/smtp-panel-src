import csv
import io
import re
import os
import tempfile
import shutil
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy import text
from sqlmodel import Session, select

from app.db import get_session, engine
from app.models import RecipientList, RecipientListRead, Recipient

router = APIRouter(prefix="/api/recipient-lists", tags=["recipient-lists"])

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+(\.[a-zA-Z0-9\-]+)+$")

CHUNK = 5_000  # rows per INSERT transaction


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


@router.get("", response_model=List[RecipientListRead])
def list_recipient_lists(session: Session = Depends(get_session)):
    lists = session.exec(select(RecipientList).order_by(RecipientList.created_at.desc())).all()
    return lists


@router.post("", response_model=RecipientListRead)
def create_recipient_list(payload: dict, session: Session = Depends(get_session)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nome obrigatório")
    rl = RecipientList(name=name)
    session.add(rl)
    session.commit()
    session.refresh(rl)
    return rl


@router.post("/{list_id}/upload")
def upload_csv(
    list_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Stream-parse a CSV/TXT file and bulk-insert valid emails in the background."""
    rl = session.get(RecipientList, list_id)
    if not rl:
        raise HTTPException(status_code=404, detail="Lista não encontrada")

    # Save the huge file to a temporary file on disk so we don't load 500MB in RAM
    fd, tmp_path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "wb") as f_out:
        file.file.seek(0)
        shutil.copyfileobj(file.file, f_out)

    background_tasks.add_task(process_csv_background, list_id, tmp_path)

    return {
        "ok": True,
        "message": "Upload recebido! Processando em segundo plano. Atualize a página em alguns instantes."
    }

def process_csv_background(list_id: int, file_path: str):
    """Background task to stream-parse and insert emails to avoid OOM and timeouts."""
    print(f"[List {list_id}] Iniciando processamento do arquivo em background...")
    try:
        # We must create a new session since the request's session is closed
        with Session(engine) as session:
            rl = session.get(RecipientList, list_id)
            if not rl:
                return

            # Get current max row_index for this list (in case of append)
            existing_max = session.exec(
                select(Recipient.row_index)
                .where(Recipient.list_id == list_id)
                .order_by(Recipient.row_index.desc())
            ).first()
            next_index = (existing_max + 1) if existing_max is not None else 0

            added = 0
            skipped_invalid = 0
            skipped_duplicate = 0

            # Load existing emails for this list to detect duplicates (using a set)
            existing_emails: set = set(
                session.exec(
                    text(f"SELECT email FROM recipient WHERE list_id = {list_id}")
                ).all()
            )
            existing_emails = {row[0].lower() for row in existing_emails}

            batch: list[dict] = []

            def flush_batch():
                nonlocal next_index
                if not batch:
                    return
                with engine.connect() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO recipient (list_id, email, status, row_index) "
                            "VALUES (:list_id, :email, :status, :row_index)"
                        ),
                        batch,
                    )
                    conn.commit()
                print(f"[List {list_id}] Lote inserido... Total salvo até agora: {added}")
                batch.clear()

            with open(file_path, "rt", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    # Find first column that looks like an email
                    email_col = None
                    for cell in row:
                        cell = cell.strip().lower()
                        if _valid_email(cell):
                            email_col = cell
                            break
                    if not email_col:
                        skipped_invalid += 1
                        continue
                    if email_col in existing_emails:
                        skipped_duplicate += 1
                        continue

                    existing_emails.add(email_col)
                    batch.append({"list_id": list_id, "email": email_col, "status": "active", "row_index": next_index})
                    next_index += 1
                    added += 1

                    if len(batch) >= CHUNK:
                        flush_batch()

                flush_batch()

            # Update counts
            total = session.exec(
                text(f"SELECT COUNT(*) FROM recipient WHERE list_id = {list_id}")
            ).one()[0]
            active = session.exec(
                text(f"SELECT COUNT(*) FROM recipient WHERE list_id = {list_id} AND status = 'active'")
            ).one()[0]

            rl.total_count = total
            rl.active_count = active
            session.add(rl)
            session.commit()
            print(f"[List {list_id}] Concluído com sucesso! Total na lista: {total}")
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print(f"[List {list_id}] ERRO GRAVE no background:\n{error_msg}")
        # Optionally, save it to a file so it's easy to read
        with open("/tmp/bg_task_error.log", "a") as f_err:
            f_err.write(error_msg + "\n")
    finally:
        # Always clean up the temporary file
        if os.path.exists(file_path):
            os.remove(file_path)


@router.get("/{list_id}", response_model=RecipientListRead)
def get_recipient_list(list_id: int, session: Session = Depends(get_session)):
    rl = session.get(RecipientList, list_id)
    if not rl:
        raise HTTPException(status_code=404, detail="Lista não encontrada")
    return rl


@router.delete("/{list_id}")
def delete_recipient_list(list_id: int, session: Session = Depends(get_session)):
    rl = session.get(RecipientList, list_id)
    if not rl:
        raise HTTPException(status_code=404, detail="Lista não encontrada")
    # Cascade delete recipients
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM recipient WHERE list_id = {list_id}"))
        conn.commit()
    session.delete(rl)
    session.commit()
    return {"ok": True}


@router.post("/{list_id}/unsubscribe")
def mark_unsubscribed(list_id: int, payload: dict, session: Session = Depends(get_session)):
    """Mark an email as unsubscribed in all lists (called by unsubscribe flow)."""
    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email obrigatório")
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE recipient SET status = 'unsubscribed' WHERE email = :email"),
            {"email": email},
        )
        conn.commit()
    # Refresh active counts for all lists containing this email
    affected_lists = session.exec(
        select(Recipient.list_id).where(Recipient.email == email).distinct()
    ).all()
    for lid in affected_lists:
        rl2 = session.get(RecipientList, lid)
        if rl2:
            active = session.exec(
                text(f"SELECT COUNT(*) FROM recipient WHERE list_id = {lid} AND status = 'active'")
            ).one()[0]
            rl2.active_count = active
            session.add(rl2)
    session.commit()
    return {"ok": True}
