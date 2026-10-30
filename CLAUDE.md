# PKG Explorer

Navegador gráfico de arquivos `.pkg` de PS4 (estilo gerenciador de arquivos compactados):
lista o conteúdo do PKG/PFS sem extrair tudo e permite extrair arquivos ou pastas individuais.

- Linguagem: Python 3, interface em Qt (PySide6).
- A lógica de leitura de PKG/PFS é adaptada do ShadPs4Plus (https://github.com/AzaharPlus/shadPS4Plus,
  baseado no shadPS4): `src/core/file_format/pkg.cpp`, `pfs.h`, `src/core/crypto/`, etc.,
  licenciado como GPL-2.0-or-later. Este projeto usa a mesma licença.
- Arquivos com código adaptado devem ter no topo `SPDX-License-Identifier: GPL-2.0-or-later`
  e indicar o arquivo de origem no shadPS4.
- O usuário fala português; responda em português.
- Comentários, docstrings e mensagens no código ficam em **inglês** (projeto público). Textos da
  interface são escritos em inglês e traduzidos em `pkg_explorer/i18n.py`.

## Fluxo de git

- Faça um commit a cada criação ou alteração de arquivo (commits pequenos, mensagem descritiva).
- Depois de commitar, faça `git push` para o `origin` (quando o remoto estiver configurado).
- Nunca adicione ao repositório arquivos `.pkg`, o AppImage do extrator ou saídas de extração
  (já estão no `.gitignore`). Os `.pkg` desta pasta servem só para testes locais.
