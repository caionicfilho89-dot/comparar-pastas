# 📁 Comparar Pastas

App offline (PyQt6) que compara duas pastas e sincroniza com segurança.

- Escolha a pasta A e a B; a varredura roda em segundo plano (com cancelar).
- Mostra: só em A, só em B, idêntico, diferente (cores, tamanhos, datas, contagem e bytes).
- Modo **rápido** (tamanho + data) ou **exato** (tamanho + SHA-256 em blocos).
- Ignorar padrões: `*.tmp, Thumbs.db, .git/` (termine com `/` para pastas).
- Ações: copiar A→B, copiar B→A, espelhar A→B. Sempre com **prévia + confirmação**.
- Cópia segura: arquivo temporário + `os.replace`; só sobrescreve arquivos marcados "diferente" e que
  não mudaram desde a comparação. Depois confere tudo (tamanho + hash no modo exato).
- Espelhar **nunca apaga de vez**: o que só existe em B só vai pra **Lixeira** (send2trash) se você
  marcar "também mandar pra Lixeira o que só existe em B" (desligado por padrão).
- Exporta o relatório em CSV ou HTML.

## Uso
```
pip install -r requirements.txt
python comparar_pastas.pyw
```
Testes: `python test_cpz.py` (usa só pastas temporárias).

Licença MIT.
