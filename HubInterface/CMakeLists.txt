#-----------------------------------------------------------------------------
set(MODULE_NAME HubInterface)

set(MODULE_PYTHON_SCRIPTS
  HubInterface.py
  Lib/HubServer.py
  Lib/ImageDisplayClient.py
  Lib/ConferenceDialog.py
  Lib/ResourceServers.py
  Lib/conference.py
  Lib/repo_paths.py
  Lib/__init__.py
  )

slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES Resources
  WITH_GENERIC_TESTS
  )

#-----------------------------------------------------------------------------
if(BUILD_TESTING)
  slicer_add_python_unittest(SCRIPT HubInterface.py)
  add_subdirectory(Testing)
endif()

#-----------------------------------------------------------------------------
# Bundle standalone components under the installed module directory.
set(_HUB_MOD_DIR "${Slicer_INSTALL_QTSCRIPTEDMODULES_LIB_DIR}/HubInterface")

set(_HUB_INSTALL_EXCLUDES
  PATTERN "__pycache__" EXCLUDE
  PATTERN "*.pyc" EXCLUDE
  PATTERN "*.pyo" EXCLUDE
  PATTERN "volview-client" EXCLUDE
  PATTERN "vtkjs-worklist-client" EXCLUDE
  PATTERN "hub-worklist-example" EXCLUDE
  PATTERN "OHIF-client" EXCLUDE
  PATTERN "slim" EXCLUDE
  PATTERN "deploy" EXCLUDE
  PATTERN "samples" EXCLUDE
  PATTERN "slicer-hub.zip" EXCLUDE
  )

install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/hub/"
        DESTINATION "${_HUB_MOD_DIR}/hub"
        ${_HUB_INSTALL_EXCLUDES}
        )

install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/python_client/"
        DESTINATION "${_HUB_MOD_DIR}/python_client"
        PATTERN "__pycache__" EXCLUDE
        PATTERN "*.pyc" EXCLUDE
        PATTERN "*.pyo" EXCLUDE
        )

install(DIRECTORY
        "${CMAKE_CURRENT_SOURCE_DIR}/resource_servers/products"
        "${CMAKE_CURRENT_SOURCE_DIR}/resource_servers/runtime"
        DESTINATION "${_HUB_MOD_DIR}/resource_servers"
        PATTERN "__pycache__" EXCLUDE
        PATTERN "*.pyc" EXCLUDE
        PATTERN "*.pyo" EXCLUDE
        )

install(FILES
        "${CMAKE_CURRENT_SOURCE_DIR}/resource_servers/resource_server.py"
        "${CMAKE_CURRENT_SOURCE_DIR}/resource_servers/hub_presets.py"
        DESTINATION "${_HUB_MOD_DIR}/resource_servers"
        )

install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/image_display/lib"
        DESTINATION "${_HUB_MOD_DIR}/image_display"
        PATTERN "__pycache__" EXCLUDE
        PATTERN "*.pyc" EXCLUDE
        PATTERN "*.pyo" EXCLUDE
        )
