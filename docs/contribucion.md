# Contribución y flujo Git

Las instrucciones de arranque están en [README](../README.md),
[red y APK](red-y-apk.md) y [despliegue](despliegue.md). La documentación de
un comando debe indicar si está distribuido por esta rama o solo existe en
un paquete privado; una función local no debe anunciarse como disponible en main.

Este repositorio publica exclusivamente el servidor. No se aceptan APK, código
decompilado, herramientas del cliente, logs, secretos ni datos de jugadores.

## Flujo de trabajo

`main` debe permanecer ejecutable. Toda feature, corrección o refactorización se
realiza en una rama corta y entra mediante Pull Request:

```powershell
git switch main
git pull --ff-only origin main
git switch -c feat/nombre-corto
```

Antes de publicar:

```powershell
git status
git diff
python -m unittest discover -s tests -v
git add <archivos concretos>
git diff --cached
git commit -m "feat(area): descripción breve"
git push -u origin feat/nombre-corto
```

El Pull Request debe explicar comportamiento, pruebas, cambios de persistencia,
riesgos y validaciones pendientes en el cliente real. Se recomienda `Squash and
merge` después de la revisión.

No usar `git push --force origin main`, `git push --all`, `git add -f`,
`git reset --hard` ni `git clean -fdx` en el flujo normal.

## Responsabilidades

- El propietario mantiene la compatibilidad con Metal Slug Attack 7.13.0, el
  contenido publicado, Photon/Luxon y la validación final en dispositivos.
- El colaborador de backend se concentra en lógica de negocio, persistencia,
  concurrencia y pruebas del servidor.
- Código, pruebas y documentación se revisan mediante Pull Request.

## Criterio de finalización

Cada tarea debe indicar archivos y comportamiento modificados, pruebas, Issue,
rama y Pull Request, migraciones, validaciones pendientes y si requiere reiniciar
el servidor o recompilar el APK. Las pruebas automáticas verifican contratos
HTTP, pero no sustituyen una prueba visual o Photon real.

## Plantilla para solicitar trabajo

```text
Objetivo:
[Qué opción, error o comportamiento debe implementarse o corregirse.]

Comportamiento esperado:
[Qué debe ver o poder hacer el jugador.]

Evidencia disponible:
[Pasos, endpoint, respuesta, captura o log relevante.]

Alcance:
[Solo servidor / servidor y documentación / incluye investigación del cliente.]

Restricciones:
[Compatibilidad, datos que conservar y cosas que no deben tocarse.]

Validación:
[Pruebas automáticas esperadas y prueba pendiente en dispositivo.]

Flujo Git:
Encárgate del flujo completo: Issue, rama, implementación, pruebas,
documentación, commit, push y Pull Request. No fusiones hasta cumplir las
revisiones requeridas.
```
