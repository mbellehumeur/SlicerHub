cmake_minimum_required(VERSION 3.20.6...3.22.6 FATAL_ERROR)

project(HubInterface)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_HOMEPAGE "https://github.com/mbellehumeur/SlicerHub")
set(EXTENSION_CONTRIBUTORS "Martin Bellehumeur")
set(EXTENSION_DESCRIPTION "Desktop Integration for Healthcare Providers and Researchers.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/HubInterfaceExtensionLogo.png")
set(EXTENSION_SCREENSHOTURLS "https://raw.githubusercontent.com/mbellehumeur/SlicerHub/main/docs/images/hub-admin.png")
set(EXTENSION_DEPENDS "NA")

#-----------------------------------------------------------------------------
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
add_subdirectory(HubInterface)

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_GENERATE_CONFIG})
include(${Slicer_EXTENSION_CPACK})
