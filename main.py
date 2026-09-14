name: Descarga diaria sensores

on:
  schedule:
    - cron: "*/5 * * * *"   # cada 5 minutos lo comprovamos
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: descarga-sensores
  cancel-in-progress: true

jobs:
  run:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run download script
        env:
          SENTILO_TOKEN: ${{ secrets.SENTILO_TOKEN }}
          SENTILO_TOKEN_FV: ${{ secrets.SENTILO_TOKEN_FV }}
        run: |
            python descarga_header.py


      - name: Commit and push changes
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add -A
          git commit -m "Auto update sensores" || echo "No changes to commit"
          git commit --allow-empty -m "wake workflow"
          git push
