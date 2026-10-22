cmake_minimum_required(VERSION 3.20.6...3.22.6 FATAL_ERROR)

project(HubInterface)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://github.com/mbellehumeur/SlicerHub")
set(EXTENSION_CONTRIBUTORS "Martin Bellehumeur")
set(EXTENSION_DESCRIPTION "WebSub infrastructure for Healthcare Providers, Educators and Researchers.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/HubServerLogo.png")
set(EXTENSION_SCREENSHOTURLS
  "https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/HubServerLogo.png https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/hub-admin.png https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/worklist-client.png https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/Screenshot-SlicerHubQt.png https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/Screenshot-classroom.png https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/Screenshot-evidence-creators.png"
  )
set(EXTENSION_DEPENDS "NA")

#-----------------------------------------------------------------------------
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
add_subdirectory(HubInterface)

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
