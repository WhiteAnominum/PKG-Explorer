# PKG Explorer

[English](README.md) | **Português**

Interface gráfica para navegar pelo conteúdo de arquivos `.pkg` de PS4 como se fossem um
arquivo compactado (zip/tar.gz) e extrair apenas os arquivos ou pastas desejados.

- Abre o PKG em menos de um segundo, mesmo com dezenas de GB: só a estrutura é lida.
- Mostra o ícone, título, Title ID, tipo (jogo base, atualização ou DLC) e versão.
- Extraia arquivos ou pastas selecionados, ou o pacote inteiro (na mesma estrutura de pastas
  usada pelo shadPS4: `CUSA00000`, `CUSA00000-patch`, `CUSA00000/<DLC>`).
- Arraste arquivos e pastas da janela para o gerenciador de arquivos, como num zip
  (veja [KDE Plasma e outros ambientes](#kde-plasma-e-outros-ambientes)).
- Duplo clique abre o arquivo no programa padrão.
- Filtro por nome.
- Associação de arquivos: dê duplo clique num `.pkg` no gerenciador de arquivos para abri-lo.
- Interface em português e inglês, escolhida pelo idioma do sistema.

Funciona com os mesmos PKGs que o extrator do shadPS4 (PKGs "fake"/fpkg).

## Instalação

### AppImage

Baixe o `PKG-Explorer-x86_64.AppImage` na página de
[Releases](https://github.com/WhiteAnominum/PKG-Explorer/releases), dê permissão de execução e
abra:

```sh
chmod +x PKG-Explorer-x86_64.AppImage
./PKG-Explorer-x86_64.AppImage
```

Na primeira vez, ele pergunta se arquivos `.pkg` devem abrir com o PKG Explorer. A linha de
comando também está disponível: `./PKG-Explorer-x86_64.AppImage --cli info jogo.pkg`.

Para gerar o AppImage a partir do código: `./packaging/appimage/build.sh` (resultado em `dist/`).

### Flatpak

Baixe o `PKG-Explorer.flatpak` na página de
[Releases](https://github.com/WhiteAnominum/PKG-Explorer/releases) e instale:

```sh
flatpak install --user PKG-Explorer.flatpak
```

Na primeira vez, ele pergunta se arquivos `.pkg` devem abrir com o PKG Explorer (veja abaixo por
que isso não é automático). O programa pede acesso aos arquivos do computador
(`--filesystem=host`) para ler PKGs em qualquer disco, extrair na pasta onde você soltar os
arquivos ao arrastar e registrar o tipo de arquivo `.pkg` de PS4.

> **Por que a associação não é automática no Flatpak?** O Flatpak exporta o tipo de arquivo
> declarado pelo app, mas remove a detecção pela assinatura do arquivo e rebaixa a prioridade da
> extensão, para que um app não possa tomar extensões de outros tipos. Como `.pkg` já é usado por
> instaladores do macOS, o sistema continuaria abrindo os PKGs de PS4 no programa errado (o Ark,
> no KDE). Ao aceitar a pergunta, o PKG Explorer instala o tipo de arquivo completo em
> `~/.local/share/mime`.

Para gerar a partir do código (requer `org.flatpak.Builder`):

```sh
flatpak run org.flatpak.Builder --user --force-clean --install build-dir \
    packaging/flatpak/io.github.WhiteAnominum.PKGExplorer.yml
```

> Se você usar o AppImage com a associação ativada **e** o Flatpak, o duplo clique abre o
> AppImage. Para usar o Flatpak, desative a associação no menu ☰ do AppImage.

### A partir do código-fonte

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

O idioma segue o do sistema; para forçar um, use `PKG_EXPLORER_LANG=en` ou `PKG_EXPLORER_LANG=pt`.

### Abrir arquivos .pkg com duplo clique

No menu (☰) da janela, marque **Abrir arquivos .pkg com o PKG Explorer**, ou rode:

```sh
.venv/bin/pkg-explorer-cli integrate      # para desfazer: unintegrate
```

Isso cria, só para o seu usuário (em `~/.local/share`), o atalho no menu de aplicativos, o
ícone e um tipo de arquivo próprio, `application/x-ps4-pkg`. A extensão `.pkg` também é usada
por instaladores do macOS; o PKG de PS4 é reconhecido pela assinatura no início do arquivo, então
os `.pkg` de macOS não são afetados.

Se você mover o PKG Explorer de lugar (ou atualizar o AppImage), a associação é corrigida
automaticamente na próxima vez que ele for aberto.

## KDE Plasma e outros ambientes

Arrastar arquivos para fora da janela funciona de jeitos diferentes dependendo do ambiente:

| | KDE Plasma | GNOME, XFCE, Cinnamon e outros |
|---|---|---|
| Como funciona | O destino (Dolphin, área de trabalho) avisa o PKG Explorer onde os itens foram soltos, e a extração é feita **direto naquela pasta** | Os itens são extraídos primeiro para uma **pasta temporária**, e só então o arrasto começa |
| Arquivos grandes | Sem espera para começar a arrastar; o progresso aparece na janela do PKG Explorer depois de soltar | É preciso esperar a extração terminar antes de arrastar; acima de 512 MB o programa pede confirmação |
| Espaço em disco | Só o destino | Destino + cópia temporária (apagada ao fechar o programa) |

Isso acontece porque não existe um padrão comum entre os ambientes para "extrair onde o usuário
soltar". O KDE tem um protocolo próprio (o mesmo usado pelo Ark), que o PKG Explorer implementa.
Fora do KDE, para arquivos grandes, prefira o botão **Extrair selecionados…**, que grava direto
na pasta escolhida.

## Créditos

- A lógica de leitura de PKG/PFS foi adaptada do
  [ShadPs4Plus PKG Extractor](https://github.com/AzaharPlus/shadPS4Plus) (AzaharPlus), que por sua
  vez usa código que fazia parte do emulador [shadPS4](https://github.com/shadps4-emu/shadPS4)
  (GPL-2.0-or-later).

## Licença

GPL-2.0-or-later. Veja [LICENSE](LICENSE).
