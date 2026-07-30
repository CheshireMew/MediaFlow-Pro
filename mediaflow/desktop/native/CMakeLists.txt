cmake_minimum_required(VERSION 3.24)

project(MediaFlowNative VERSION 2.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.11 REQUIRED COMPONENTS Core Gui Qml Quick QuickPrivate)

qt_add_plugin(mediaflownativeplugin
    CLASS_NAME MediaFlowNativePlugin
    MltPreviewItem.cpp
    MltPreviewItem.h
    MltRuntime.cpp
    MltRuntime.h
    plugin.cpp
)

target_link_libraries(mediaflownativeplugin PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
    Qt6::QuickPrivate
)

if(WIN32)
    target_link_libraries(mediaflownativeplugin PRIVATE dxgi)
endif()

set_target_properties(mediaflownativeplugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/qml/MediaFlow/Native"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/qml/MediaFlow/Native"
    OUTPUT_NAME "mediaflownativeplugin"
)

configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/qmldir"
    "${CMAKE_BINARY_DIR}/qml/MediaFlow/Native/qmldir"
    COPYONLY
)
