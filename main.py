import os
import time
from binascii import hexlify
import unicodedata
from functools import partial
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import nfc


DEBUG = os.environ.get("DEBUG") == "TRUE"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or ""
GAS_API_URL = os.environ.get("GAS_API_URL") or ""

if WEBHOOK_URL is None or GAS_API_URL is None:
  print("environment variable WEBHOOK_URL or GAS_API_URL is not set.")
  exit(1)

if DEBUG:
  print("====== DEBUG MODE ======")

stay_users = {}


def hankaku_to_hiragana(text):
  zenkaku_katakana = unicodedata.normalize('NFKC', text)
  return ''.join(chr(ord(char) - ord('ァ') + ord('ぁ')) if 'ァ' <=
                 char <= 'ン' else char for char in zenkaku_katakana)


def post_discord(message: str):
  headers = {
      "Content-Type": "application/json",
      "User-Agent": "DiscordBot (private use) Python-urllib/3.10",
  }
  data = {"content": message}
  request = Request(
      WEBHOOK_URL,
      data=json.dumps(data).encode(),
      headers=headers,
  )

  with urlopen(request) as res:
    assert res.getcode() == 204


def post_gas(student_id: str, name: str, email: str, kitade: bool):
  headers = {"Content-Type": "application/x-www-form-urlencoded"}
  params = urlencode({
      'student_id': student_id,
      'name': name,
      'email': email,
      'kitade': kitade,
      'debug': DEBUG,
  }).encode()
  request = Request(
      GAS_API_URL,
      data=params,
      headers=headers,
  )
  with urlopen(request) as res:
    assert res.getcode() == 204


def hello(student_id, name, email):
  nick = hankaku_to_hiragana(name.split(" ")[0])
  if student_id in stay_users:
    post_discord(f"{nick}が帰るで〜")
    post_gas(student_id, name, email, False)
    stay_users.pop(student_id)
    return
  stay_users[student_id] = name
  post_discord(f"{nick}が来たで〜")
  post_gas(student_id, name, email, True)


class NFCReader:
  def __init__(self):
    self.on_card = False  # タグ接続状態を保持

  def on_connect(self, tag):
    """タグが接続されたときの処理"""
    self.on_card = True
    print("Connected to NFC tag.")

    try:
      system_codes = tag.request_system_code()
      collected_data = {}

      # Read data from system code 0x8aaf
      if 0x8AAF in system_codes:
        collected_data.update(self.read_system(tag, 0x8AAF))

      # Skipping system code 0xfe00
      if 0xFE00 in system_codes:
        print("Skipping System Code 0xfe00 due to known issues.")

      extracted_info = self.extract_information(collected_data)
      self.print_extracted_info(extracted_info)

    except Exception as e:
      print(f"Error communicating with NFC tag: {e}")

    return True

  def on_release(self, tag):
    """タグがリリースされたときの処理"""
    self.on_card = False
    print("Tag removed from the NFC reader.")
    return True

  def read_system(self, tag, system_code):
    """Reads NFC tag data for a specific system code."""
    collected_data = {}
    service_codes_and_blocks = self.get_service_codes_and_blocks(system_code)

    try:
      idm, pmm = tag.polling(system_code=system_code)
      print(
          f"System Code: {hex(system_code)}, IDm: {hexlify(idm)}, PMm: {hexlify(pmm)}"
      )

      for service_code, blocks in service_codes_and_blocks.items():
        sc = nfc.tag.tt3.ServiceCode(service_code >> 6, service_code & 0x3F)
        for block in blocks:
          bc = nfc.tag.tt3.BlockCode(block, service=0)
          try:
            data = tag.read_without_encryption([sc], [bc])
            print(
                f"Service Code: {hex(service_code)}, Block: {block}, Data: {hexlify(data)}"
            )
            collected_data[(service_code, block)] = data
          except Exception as e:
            print(
                f"Failed to read Service Code: {hex(service_code)}, Block: {block}. Error: {e}"
            )
    except Exception as e:
      print(f"Error polling system code {hex(system_code)}: {e}")

    return collected_data

  def get_service_codes_and_blocks(self, system_code):
    """Returns service codes and their respective blocks based on system code."""
    if system_code == 0x8AAF:
      return {
          0x10B: [0, 1, 2, 3],
          0x20B: [0, 1, 2, 3, 4],
          0x309: [0, 1, 2, 3, 4, 5, 6, 7],
          0x50B: [0, 1, 2, 3, 4, 5, 6, 7],
      }
    return {}

  def extract_information(self, collected_data):
    """Extracts student information from collected NFC data."""

    def decode_data(data, encoding="ascii"):
      try:
        return bytearray(data).decode(encoding).strip()
      except:
        return None

    extracted_info = {
        "student_id": decode_data(collected_data.get((0x10B, 0), b""))[4:14],
        "name": decode_data(
            collected_data.get((0x10B, 1), b""), encoding="shift-jis"
        ),
        "expiration_date": self.decode_expiration_date(
            collected_data.get((0x10B, 3))
        ),
        "department_personal_code": decode_data(
            collected_data.get((0x20B, 1), b"")
        )[:7],
    }

    if extracted_info["student_id"] and extracted_info["department_personal_code"]:
      extracted_info["email"] = (
          f"{extracted_info['student_id']}{extracted_info['department_personal_code'][-1]}@kindai.ac.jp"
      )
    else:
      extracted_info["email"] = None

    return extracted_info

  def decode_expiration_date(self, data):
    """Decodes expiration date assuming it is stored as YYYYMMDD."""
    try:
      year, month, day = data[:4].decode(
      ), data[4:6].decode(), data[6:8].decode()
      return f"{year}-{month}-{day}"
    except:
      return None

  def print_extracted_info(self, info):
    """Prints the extracted student information."""
    print("\n=== Extracted Student Information ===")
    for key, value in info.items():
      print(f"{key.capitalize().replace('_', ' ')}: {value}")
    print("======================================\n")
    hello(info["student_id"], info["name"], info["email"])

  def after(self, started, timeout):
    """接続後のタイムアウトを管理"""
    return time.time() - started > timeout and not self.on_card

  def run(self, timeout=5):
    """NFCリーダーを開始"""
    with nfc.ContactlessFrontend("usb") as clf:
      print("Ready to read NFC tags. Touch a tag to start.")
      clf.connect(
          rdwr={"on-connect": self.on_connect, "on-release": self.on_release},
          terminate=partial(self.after, time.time(), timeout),
      )


if __name__ == "__main__":
  try:
    while True:
      reader = NFCReader()
      reader.run(timeout=5)
  except KeyboardInterrupt:
    print("NFC reader stopped.")
