import pdfplumber, json

PATH = "/tmp/llm_quiz/res_2.pdf"   # use the path shown in your job JSON
print("Opening:", PATH)

with pdfplumber.open(PATH) as pdf:
    print("Pages:", len(pdf.pages))

    page = pdf.pages[1]   # page 2

    print("\n--- page.extract_table() ---")
    print(json.dumps(page.extract_table(), indent=2))

    print("\n--- page.extract_tables() ---")
    print(json.dumps(page.extract_tables(), indent=2))

    print("\n--- page.extract_text() ---")
    print(page.extract_text())
