# SPDX-License-Identifier: GPL-2.0-or-later
"""Tradução da interface. Os textos no código estão em inglês; aqui fica o português.

O idioma vem do sistema (LANGUAGE, LC_ALL, LC_MESSAGES, LANG): português para pt_*, inglês
para o resto. PKG_EXPLORER_LANG=en|pt força um idioma.
"""

from __future__ import annotations

import os


def _system_language() -> str:
    forced = os.environ.get("PKG_EXPLORER_LANG")
    if forced:
        return forced
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return value.split(":")[0]
    return ""


LANGUAGE = "pt" if _system_language().lower().startswith("pt") else "en"

PT = {
    # Leitor de PKG
    "read beyond end of file (offset 0x{offset:X}, size 0x{size:X})":
        "leitura fora do arquivo (offset 0x{offset:X}, tamanho 0x{size:X})",
    "this does not look like a valid PKG file": "não parece ser um arquivo PKG válido",
    "the PKG size is different from expected (incomplete or corrupted file?)":
        "o tamanho do PKG é diferente do esperado (arquivo incompleto ou corrompido?)",
    "the content is larger than the PKG": "o conteúdo é maior que o PKG",
    "invalid param.sfo": "param.sfo inválido",
    "the PKG has no key entries (ENTRY_KEYS / IMAGE_KEY)":
        "o PKG não tem as entradas de chave (ENTRY_KEYS / IMAGE_KEY)",
    "could not decrypt the PKG keys (only fake/fpkg PKGs are supported)":
        "não foi possível decifrar as chaves do PKG (só PKGs fake/fpkg são suportados)",
    "PFSC image not found inside the PKG": "imagem PFSC não encontrada dentro do PKG",
    "PFSC block larger than expected": "bloco PFSC maior que o esperado",
    "'uroot' directory not found in the PFS": "diretório 'uroot' não encontrado no PFS",
    "'{path}' is a directory": "'{path}' é um diretório",
    "invalid file name in the PKG: {name!r}": "nome de arquivo inválido no PKG: {name!r}",
    "refusing to write outside the destination folder: {path}":
        "gravação fora da pasta de destino recusada: {path}",
    # Linha de comando
    "Browse PS4 PKGs": "Explora PKGs de PS4",
    "show information about the PKG": "mostra informações do PKG",
    "list the files in the PKG": "lista os arquivos do PKG",
    "extract files/folders (or everything, if none is given)":
        "extrai arquivos/pastas (ou tudo, se nenhum for dado)",
    "paths inside the PKG, e.g. sce_sys/icon0.png": "caminhos dentro do PKG, ex.: sce_sys/icon0.png",
    "destination folder": "pasta de destino",
    "make .pkg files open with PKG Explorer and add it to the applications menu":
        "associa arquivos .pkg ao PKG Explorer e cria o atalho no menu",
    "undo 'integrate'": "desfaz o 'integrate'",
    "Done. PS4 .pkg files now open with: {command}":
        "Pronto. Arquivos .pkg de PS4 agora abrem com: {command}",
    "Integration removed.": "Integração removida.",
    "Nothing to remove.": "Nada para remover.",
    "error: {error}": "erro: {error}",
    "not found in the PKG: {path}": "não encontrado no PKG: {path}",
    "extracted: {path}": "extraído: {path}",
    "Title:": "Título:",
    "Type:": "Tipo:",
    "category": "categoria",
    "Version:": "Versão:",
    "Files:": "Arquivos:",
    # Interface gráfica
    "Name": "Nome",
    "Size": "Tamanho",
    "Type": "Tipo",
    "Game": "Jogo base",
    "Update": "Atualização",
    "Folder": "Pasta",
    "File": "Arquivo",
    "{ext} file": "Arquivo {ext}",
    "(everything)": "(tudo)",
    "Extracting…": "Extraindo…",
    "Extracting": "Extraindo",
    "Extracting:\n{path}": "Extraindo:\n{path}",
    "Cancel": "Cancelar",
    "Extraction error": "Erro na extração",
    "Open PKG…": "Abrir PKG…",
    "Open PKG": "Abrir PKG",
    "Extract selected…": "Extrair selecionados…",
    "Extract all…": "Extrair tudo…",
    "Open .pkg files with PKG Explorer": "Abrir arquivos .pkg com o PKG Explorer",
    "About": "Sobre",
    "Main": "Principal",
    "Filter by name…": "Filtrar por nome…",
    "<h3>No PKG open</h3>Use <b>Open PKG…</b> or drag a .pkg file here.":
        "<h3>Nenhum PKG aberto</h3>Use <b>Abrir PKG…</b> ou arraste um arquivo .pkg para cá.",
    "PS4 packages (*.pkg *.PKG);;All files (*)": "Pacotes PS4 (*.pkg *.PKG);;Todos os arquivos (*)",
    "Could not open": "Não foi possível abrir",
    "Version": "Versão",
    "Contents": "Conteúdo",
    "{count} files, {size}": "{count} arquivos, {size}",
    "{count} item(s) selected, {size}": "{count} item(ns) selecionado(s), {size}",
    "Replace?": "Substituir?",
    "Already exists in {dest}:\n\n{names}\n\nReplace?": "Já existe(m) em {dest}:\n\n{names}\n\nSubstituir?",
    "Extract": "Extrair",
    "Select one or more files or folders.": "Selecione um ou mais arquivos ou pastas.",
    "Extract to…": "Extrair para…",
    "Extract all to…": "Extrair tudo para…",
    "Extracted to {path}": "Extraído para {path}",
    "Folder already exists": "Pasta já existe",
    "{path}\n\nalready exists. Extract over it (files with the same name will be replaced)?":
        "{path}\n\njá existe. Extrair por cima (arquivos com o mesmo nome serão substituídos)?",
    "Please wait": "Aguarde",
    "An extraction is already in progress.": "Já existe uma extração em andamento.",
    "Unsupported destination": "Destino não suportado",
    "Can only extract to local folders.\n\nDestination: {path}":
        "Só é possível extrair para pastas locais.\n\nDestino: {path}",
    "Large file": "Arquivo grande",
    "This will first extract {size} to a temporary folder. Continue?\n\n"
    "Tip: for large files, use “Extract selected…”.":
        "Isso vai extrair {size} para uma pasta temporária antes. Continuar?\n\n"
        "Dica: para arquivos grandes, use “Extrair selecionados…”.",
    "Open": "Abrir",
    "Copy path": "Copiar caminho",
    "Done: PS4 .pkg files now open with PKG Explorer.":
        "Pronto: arquivos .pkg de PS4 agora abrem com o PKG Explorer.",
    ".pkg file association removed.": "Associação de arquivos .pkg removida.",
    "Error": "Erro",
    "Open .pkg files": "Abrir arquivos .pkg",
    "Do you want PS4 .pkg files to open with PKG Explorer when you double-click them?":
        "Quer que arquivos .pkg de PS4 abram com o PKG Explorer ao dar duplo clique?",
    "This also adds a shortcut to the applications menu. ":
        "Também cria um atalho no menu de aplicativos. ",
    "You can change this later in the window's ☰ menu.":
        "Dá para mudar isso depois no menu ☰ da janela.",
    "Yes": "Sim",
    "Not now": "Agora não",
    "About PKG Explorer": "Sobre o PKG Explorer",
    "Browse the contents of PS4 PKGs and extract only what you need.":
        "Navegue pelo conteúdo de PKGs de PS4 e extraia só o que quiser.",
    "PKG/PFS reading adapted from {extractor}, based on {emulator}.":
        "Leitura de PKG/PFS adaptada do {extractor}, baseado no {emulator}.",
    "License: GPL-2.0-or-later.": "Licença: GPL-2.0-or-later.",
}


def _(text: str) -> str:
    if LANGUAGE == "pt":
        return PT.get(text, text)
    return text
