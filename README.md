# PKG Explorer

Interface gráfica para navegar pelo conteúdo de arquivos `.pkg` de PS4 como se fossem um
arquivo compactado (zip/tar.gz) e extrair apenas os arquivos ou pastas desejados.

- Abre o PKG em menos de um segundo, mesmo com dezenas de GB: só a estrutura é lida.
- Mostra o ícone, título, Title ID, tipo (jogo base, atualização ou DLC) e versão.
- Extraia arquivos ou pastas selecionados, ou o pacote inteiro (na mesma estrutura de pastas
  usada pelo shadPS4: `CUSA00000`, `CUSA00000-patch`, `CUSA00000/<DLC>`).
- Arraste arquivos da janela para o gerenciador de arquivos, ou dê duplo clique para abrir.
- Filtro por nome.

Funciona com os mesmos PKGs que o extrator do shadPS4 (PKGs "fake"/fpkg).

## Instalação

Requer Python 3.10 ou mais recente.

```sh
git clone https://github.com/WhiteAnominum/PKG-Explorer.git
cd PKG-Explorer
python3 -m venv .venv
.venv/bin/pip install .
```

## Uso

Interface gráfica:

```sh
.venv/bin/pkg-explorer                 # ou: .venv/bin/pkg-explorer jogo.pkg
```

Linha de comando:

```sh
.venv/bin/pkg-explorer-cli info jogo.pkg
.venv/bin/pkg-explorer-cli list jogo.pkg
.venv/bin/pkg-explorer-cli extract jogo.pkg sce_sys/icon0.png eboot.bin -o pasta_destino
.venv/bin/pkg-explorer-cli extract jogo.pkg -o pasta_destino     # tudo
```

## Créditos

- A lógica de leitura de PKG/PFS foi adaptada do
  [ShadPs4Plus PKG Extractor](https://github.com/AzaharPlus/shadPS4Plus) (AzaharPlus), que por sua
  vez usa código que fazia parte do emulador [shadPS4](https://github.com/shadps4-emu/shadPS4)
  (GPL-2.0-or-later).

## Licença

GPL-2.0-or-later. Veja [LICENSE](LICENSE).
